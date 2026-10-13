"""
app.py — Servidor local del Índice de Complejidad de Navegación (ICN)

Arranque:
    python3 app.py
    -> http://127.0.0.1:8000

Todo corre en tu máquina: el scraping, el modelo de embeddings y los cálculos.
No se envía nada a ningún servicio externo salvo las peticiones HTTP a los
portales que tú agregues (y, la primera vez, la descarga del modelo desde
Hugging Face).
"""

from __future__ import annotations

import json
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

from core.amplitud_ref import amplitudes_de, n_ref_de_corpus  # noqa: E402
from core.catalogo import CATALOGO  # noqa: E402
from core.complejidad import PESOS_POR_DEFECTO, evaluar  # noqa: E402
from core.estadisticos import analizar_corpus  # noqa: E402
from core.scraper import extraer_menus  # noqa: E402
from core.similitud import (MODELOS_SUGERIDOS, MODELO_POR_DEFECTO,  # noqa: E402
                            calcular_similitudes, crear_embedder)

DATOS = RAIZ / "datos"
DATOS.mkdir(exist_ok=True)
ARCHIVO_PORTALES = DATOS / "portales.json"
ARCHIVO_RESULTADOS = DATOS / "resultados.json"

app = FastAPI(title="ICN · Índice de Complejidad de Navegación")


# --------------------------------------------------------------------------- #
# Estado en memoria
# --------------------------------------------------------------------------- #

class Trabajo:
    """Estado del análisis en curso. Un solo trabajo a la vez."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reiniciar()

    def reiniciar(self) -> None:
        self.corriendo = False
        self.terminado = False
        self.cancelado = False
        self.pct = 0.0
        self.fase = ""
        self.mensaje = ""
        self.bitacora: list[dict] = []
        self.error: Optional[str] = None
        self.inicio: Optional[float] = None

    def avisar(self, pct: float, fase: str, mensaje: str,
               nivel: str = "info") -> None:
        with self.lock:
            self.pct = round(max(0.0, min(100.0, pct)), 1)
            self.fase = fase
            self.mensaje = mensaje
            self.bitacora.append({
                "t": round(time.time() - (self.inicio or time.time()), 1),
                "pct": self.pct, "fase": fase, "texto": mensaje, "nivel": nivel,
            })
            if len(self.bitacora) > 400:
                del self.bitacora[:-400]

    def instantanea(self) -> dict:
        with self.lock:
            return {
                "corriendo": self.corriendo,
                "terminado": self.terminado,
                "pct": self.pct,
                "fase": self.fase,
                "mensaje": self.mensaje,
                "error": self.error,
                "bitacora": self.bitacora[-60:],
                "segundos": round(time.time() - self.inicio, 1) if self.inicio else 0,
            }


TRABAJO = Trabajo()


def limpiar_json(obj):
    """
    Sustituye NaN e infinitos por None antes de serializar.

    Aparecen de forma legítima: la correlación de Spearman queda indefinida
    cuando una serie es constante, y la alfa de Cronbach cuando la varianza
    total es cero. JSON no admite esos valores, así que se convierten en null
    en lugar de romper la respuesta.
    """
    import math
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: limpiar_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [limpiar_json(v) for v in obj]
    return obj


def cargar_portales() -> list[dict]:
    if ARCHIVO_PORTALES.exists():
        return json.loads(ARCHIVO_PORTALES.read_text(encoding="utf-8"))
    return []


def guardar_portales(lista: list[dict]) -> None:
    ARCHIVO_PORTALES.write_text(json.dumps(lista, ensure_ascii=False, indent=2),
                                encoding="utf-8")


def normalizar_url(url: str) -> str:
    """
    Acepta una dirección web o la ruta a un HTML guardado en disco.

    Admitir archivos locales no es solo comodidad: para portales cuyo
    `robots.txt` restringe el rastreo, el procedimiento correcto es guardar una
    vez el DOM renderizado y analizarlo desde ahí, lo que además hace el
    experimento reproducible.
    """
    url = (url or "").strip()
    if not url:
        raise ValueError("La dirección está vacía.")

    ruta = Path(url).expanduser()
    if ruta.exists() and ruta.is_file():
        return str(ruta.resolve())

    if "://" not in url:
        url = "https://" + url
    p = urlparse(url)
    if not p.netloc:
        raise ValueError(f"No se reconoce «{url}» como una dirección web.")
    return url


def clave_desde(url: str, nombre: str) -> str:
    base = (nombre or urlparse(url).netloc).strip()
    limpio = "".join(ch for ch in base if ch.isalnum())[:12].upper()
    return limpio or "PORTAL"


# --------------------------------------------------------------------------- #
# Gestión de portales
# --------------------------------------------------------------------------- #

class PortalEntrada(BaseModel):
    url: str
    nombre: str = ""


class LoteEntrada(BaseModel):
    texto: str


@app.get("/api/portales")
def listar_portales() -> dict:
    return {"portales": cargar_portales(), "catalogo_disponible": len(CATALOGO)}


@app.post("/api/portales")
def agregar_portal(entrada: PortalEntrada) -> dict:
    try:
        url = normalizar_url(entrada.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    lista = cargar_portales()
    if any(p["url"].rstrip("/") == url.rstrip("/") for p in lista):
        raise HTTPException(409, "Ese portal ya está en la lista.")

    nombre = (entrada.nombre.strip()
              or urlparse(url).netloc.replace("www.", "")
              or Path(url).stem)
    lista.append({
        "id": f"p{int(time.time() * 1000) % 10_000_000}{len(lista)}",
        "clave": clave_desde(url, nombre),
        "nombre": nombre,
        "url": url,
        "agregado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    guardar_portales(lista)
    return {"portales": lista, "agregado": nombre}


@app.post("/api/portales/lote")
def agregar_lote(entrada: LoteEntrada) -> dict:
    """
    Acepta una dirección por línea. Admite «Nombre, URL», «Nombre<tab>URL»
    o solo la URL.
    """
    lista = cargar_portales()
    vistos = {p["url"].rstrip("/") for p in lista}
    nuevos, rechazados = 0, []

    for n, linea in enumerate(entrada.texto.splitlines()):
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        nombre = ""
        if "\t" in linea:
            nombre, _, resto = linea.partition("\t")
        elif "," in linea and "://" in linea.split(",")[-1]:
            nombre, _, resto = linea.rpartition(",")
        else:
            resto = linea
        try:
            url = normalizar_url(resto)
        except ValueError:
            rechazados.append({"linea": n + 1, "texto": linea[:60]})
            continue
        if url.rstrip("/") in vistos:
            continue
        vistos.add(url.rstrip("/"))
        nombre = (nombre.strip() or urlparse(url).netloc.replace("www.", "")
                  or Path(url).stem)
        lista.append({
            "id": f"p{int(time.time() * 1000) % 10_000_000}{len(lista)}",
            "clave": clave_desde(url, nombre),
            "nombre": nombre, "url": url,
            "agregado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        nuevos += 1

    guardar_portales(lista)
    return {"portales": lista, "nuevos": nuevos, "rechazados": rechazados}


@app.post("/api/portales/archivo")
async def agregar_archivo(archivo: UploadFile = File(...)) -> dict:
    contenido = (await archivo.read()).decode("utf-8", errors="ignore")
    return agregar_lote(LoteEntrada(texto=contenido))


@app.post("/api/portales/catalogo")
def agregar_catalogo() -> dict:
    texto = "\n".join(f"{nombre}\t{url}" for _, nombre, url in CATALOGO)
    return agregar_lote(LoteEntrada(texto=texto))


@app.delete("/api/portales/{pid}")
def borrar_portal(pid: str) -> dict:
    lista = [p for p in cargar_portales() if p["id"] != pid]
    guardar_portales(lista)
    return {"portales": lista}


@app.delete("/api/portales")
def vaciar_portales() -> dict:
    guardar_portales([])
    return {"portales": []}


# --------------------------------------------------------------------------- #
# Análisis
# --------------------------------------------------------------------------- #

class OpcionesAnalisis(BaseModel):
    modelo: str = MODELO_POR_DEFECTO
    render: bool = False
    interactivo: bool = False
    percentil: float = 95.0
    demo: bool = False            # embedder de hashing, sin descargar pesos
    ignorar_robots: bool = False
    pistas: str = "sin-herencia"


def _trabajo_analisis(portales: list[dict], op: OpcionesAnalisis) -> None:
    """
    El orden es obligatorio: extraer TODO -> n_ref del corpus -> evaluar TODO
    con el mismo n_ref. Evaluar cada portal con su propio n_ref los haría
    mutuamente incomparables.
    """
    t0 = time.time()
    TRABAJO.inicio = t0
    n = len(portales)
    arboles: list[tuple[dict, dict]] = []

    try:
        # ---- Fase 1: extracción (0 - 45 %) -----------------------------
        TRABAJO.avisar(1, "extraccion", f"Extrayendo la navegación de {n} portales")
        for i, p in enumerate(portales):
            if TRABAJO.cancelado:
                raise RuntimeError("Análisis cancelado.")
            base = 1 + 44 * i / n
            TRABAJO.avisar(base, "extraccion", f"Leyendo {p['nombre']}")
            try:
                if op.interactivo:
                    from core.dinamico import extraer_menus_interactivo
                    datos = extraer_menus_interactivo(
                        p["url"], verificar_robots=not op.ignorar_robots)
                else:
                    datos = extraer_menus(
                        p["url"], render_js=op.render,
                        verificar_robots=not op.ignorar_robots)
            except Exception as exc:
                TRABAJO.avisar(base, "extraccion",
                               f"{p['nombre']}: {type(exc).__name__} — se omite",
                               nivel="alerta")
                continue

            if datos["n_submenus"] == 0:
                TRABAJO.avisar(base, "extraccion",
                               f"{p['nombre']}: sin submenús, se omite "
                               f"(prueba con renderizado o modo interactivo)",
                               nivel="alerta")
                continue

            arboles.append((p, datos))
            TRABAJO.avisar(1 + 44 * (i + 1) / n, "extraccion",
                           f"{p['nombre']}: {datos['n_menus']} menús, "
                           f"{datos['n_submenus']} submenús", nivel="ok")

        if len(arboles) < 1:
            raise RuntimeError(
                "Ningún portal entregó submenús. Revisa las direcciones o "
                "activa el renderizado de JavaScript.")

        # ---- Fase 2: modelo (45 - 55 %) --------------------------------
        TRABAJO.avisar(46, "modelo",
                       "Cargando el modelo de embeddings "
                       "(la primera vez descarga ~450 MB)")
        emb = crear_embedder(op.modelo, dry_run=op.demo)
        TRABAJO.avisar(55, "modelo", f"Modelo listo: {emb.nombre}", nivel="ok")

        # ---- Fase 3: n_ref del corpus (55 - 60 %) ----------------------
        TRABAJO.avisar(56, "nref",
                       f"Calculando la amplitud de referencia "
                       f"(percentil {op.percentil:g} del corpus)")
        rutas = []
        for p, datos in arboles:
            r = DATOS / "arboles" / f"{p['id']}.json"
            r.parent.mkdir(parents=True, exist_ok=True)
            r.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
            rutas.append(r)
        meta_nref = n_ref_de_corpus(rutas, op.percentil)
        ruta_nref = DATOS / "n_ref.json"
        ruta_nref.write_text(json.dumps(meta_nref, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        TRABAJO.avisar(60, "nref",
                       f"n_ref = {meta_nref['n_ref']} · saturan "
                       f"{meta_nref['n_saturan']} de "
                       f"{meta_nref['n_observaciones']} menús "
                       f"({100 * meta_nref['tasa_saturacion']:.1f}%)", nivel="ok")

        # ---- Fase 4: evaluación (60 - 92 %) ----------------------------
        filas, detalle = [], {}
        m = len(arboles)
        for i, (p, datos) in enumerate(arboles):
            if TRABAJO.cancelado:
                raise RuntimeError("Análisis cancelado.")
            TRABAJO.avisar(60 + 32 * i / m, "evaluacion", f"Midiendo {p['nombre']}")
            try:
                pares, art = calcular_similitudes(datos, emb, modo_pistas=op.pistas)
                rep = evaluar(datos, pares, art, n_ref=str(ruta_nref))
            except Exception as exc:
                TRABAJO.avisar(60 + 32 * i / m, "evaluacion",
                               f"{p['nombre']}: {type(exc).__name__} — se omite",
                               nivel="alerta")
                continue

            menus = rep["menus"]
            pond = sum(mm["n_hijos"] for mm in menus) or 1
            prom = {c: round(sum(mm[c] * mm["n_hijos"] for mm in menus) / pond, 4)
                    for c in ("D", "V", "X", "A", "P")}
            filas.append({
                "id": p["id"], "clave": p["clave"], "nombre": p["nombre"],
                "url": p["url"],
                "n_menus": datos["n_menus"], "n_submenus": datos["n_submenus"],
                "profundidad": datos["profundidad_max"],
                "ICN": rep["ICN_sitio"], "ICN_menus": rep["ICN_menus"],
                "A0": rep["amplitud_primer_nivel"],
                "pi_media": rep["pertenencia_media"],
                "mal_ubicados": rep["submenus_mal_ubicados"],
                "prop_mal_ubicados": round(
                    rep["submenus_mal_ubicados"] / max(datos["n_submenus"], 1), 4),
                "migracion_global": rep["tasa_migracion_global"],
                "mu0": rep["calibracion"]["mu0"],
                "sigma0": rep["calibracion"]["sigma0"],
                "satura": rep["amplitud_referencia"]["menus_que_saturan"],
                "pi_todos": [s["pertenencia"] for s in rep["submenus"]],
                "n_nulo": rep["precision"]["n_nulo"],
                "pi_ic_bajo": rep["precision"]["ic_bajo"],
                "pi_ic_alto": rep["precision"]["ic_alto"],
                "pi_ic_ancho": rep["precision"]["ancho"],
                "precision_ok": rep["precision"]["precision_suficiente"],
                "precision_motivos": rep["precision"]["motivos"],
                "n_pares": rep["precision"]["n_pares"],
                **prom,
            })
            detalle[p["id"]] = {
                "menus": menus,
                "peores": rep["submenus"][:12],
            }
            TRABAJO.avisar(60 + 32 * (i + 1) / m, "evaluacion",
                           f"{p['nombre']}: ICN {rep['ICN_sitio']:.3f} · "
                           f"π {rep['pertenencia_media']:.3f}", nivel="ok")

        if not filas:
            raise RuntimeError("Ningún portal pudo evaluarse.")

        # ---- Fase 5: estadística de corpus (92 - 100 %) ----------------
        # Los portales cuyo intervalo de pi es demasiado ancho no entran en la
        # estadística de corpus: su escala está mal determinada porque se
        # calibró con muy pocos pares nulos. Se muestran en la tabla, marcados.
        TRABAJO.avisar(93, "estadistica", "Calculando estadísticos de corpus")
        fiables = [f for f in filas if f.get("precision_ok") is not False]
        aparte = [f for f in filas if f.get("precision_ok") is False]
        if aparte:
            TRABAJO.avisar(95, "estadistica",
                           f"{len(aparte)} portales con precisión insuficiente "
                           f"quedan fuera del agregado: "
                           f"{', '.join(f['nombre'] for f in aparte)}",
                           nivel="alerta")
        stats = analizar_corpus(fiables if len(fiables) >= 2 else filas,
                                dict(PESOS_POR_DEFECTO))
        stats["excluidos_por_precision"] = [
            {"nombre": f["nombre"], "n_submenus": f["n_submenus"],
             "n_nulo": f["n_nulo"], "ancho_ic": f["pi_ic_ancho"]} for f in aparte]

        resultados = {
            "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "segundos": round(time.time() - t0, 1),
            "opciones": op.model_dump(),
            "modelo": emb.nombre,
            "n_ref": meta_nref,
            "pesos": dict(PESOS_POR_DEFECTO),
            "portales": sorted(filas, key=lambda f: -f["ICN"]),
            "detalle": detalle,
            "estadisticos": stats,
            "omitidos": len(portales) - len(filas),
        }
        ARCHIVO_RESULTADOS.write_text(
            json.dumps(limpiar_json(resultados), ensure_ascii=False, indent=2),
            encoding="utf-8")

        TRABAJO.avisar(100, "listo",
                       f"Listo: {len(filas)} portales medidos en "
                       f"{time.time() - t0:.0f} s", nivel="ok")
    except Exception as exc:
        TRABAJO.error = f"{type(exc).__name__}: {exc}"
        TRABAJO.avisar(TRABAJO.pct, "error", TRABAJO.error, nivel="error")
        traceback.print_exc()
    finally:
        with TRABAJO.lock:
            TRABAJO.corriendo = False
            TRABAJO.terminado = True


@app.post("/api/analizar")
def analizar(op: OpcionesAnalisis) -> dict:
    if TRABAJO.corriendo:
        raise HTTPException(409, "Ya hay un análisis en curso.")
    portales = cargar_portales()
    if not portales:
        raise HTTPException(400, "Agrega al menos un portal antes de analizar.")

    TRABAJO.reiniciar()
    TRABAJO.corriendo = True
    TRABAJO.inicio = time.time()
    TRABAJO.avisar(0, "inicio", f"Preparando {len(portales)} portales")
    threading.Thread(target=_trabajo_analisis, args=(portales, op),
                     daemon=True).start()
    return {"iniciado": True, "n": len(portales)}


@app.post("/api/cancelar")
def cancelar() -> dict:
    TRABAJO.cancelado = True
    return {"cancelado": True}


@app.get("/api/estado")
def estado() -> dict:
    return TRABAJO.instantanea()


@app.get("/api/resultados")
def resultados() -> Any:
    if not ARCHIVO_RESULTADOS.exists():
        raise HTTPException(404, "Todavía no hay resultados.")
    return JSONResponse(json.loads(ARCHIVO_RESULTADOS.read_text(encoding="utf-8")))


@app.get("/api/exportar/csv")
def exportar_csv() -> PlainTextResponse:
    if not ARCHIVO_RESULTADOS.exists():
        raise HTTPException(404, "Todavía no hay resultados.")
    r = json.loads(ARCHIVO_RESULTADOS.read_text(encoding="utf-8"))
    cols = ["clave", "nombre", "url", "n_menus", "n_submenus", "profundidad",
            "D", "V", "X", "A", "P", "ICN", "percentil_ICN", "cuartil",
            "pi_media", "mal_ubicados", "prop_mal_ubicados", "migracion_global",
            "mu0", "sigma0", "A0", "satura"]
    lineas = [";".join(cols)]
    for p in r["portales"]:
        lineas.append(";".join(str(p.get(c, "")).replace(";", ",") for c in cols))
    return PlainTextResponse(
        "\ufeff" + "\n".join(lineas), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=icn_corpus.csv"})


@app.get("/api/exportar/json")
def exportar_json() -> FileResponse:
    if not ARCHIVO_RESULTADOS.exists():
        raise HTTPException(404, "Todavía no hay resultados.")
    return FileResponse(ARCHIVO_RESULTADOS, media_type="application/json",
                        filename="icn_resultados.json")


@app.get("/api/modelos")
def modelos() -> dict:
    return {"sugeridos": MODELOS_SUGERIDOS, "defecto": MODELO_POR_DEFECTO}


# --------------------------------------------------------------------------- #
# Interfaz
# --------------------------------------------------------------------------- #

app.mount("/", StaticFiles(directory=RAIZ / "web", html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    print("\n  ICN · Índice de Complejidad de Navegación")
    print("  Abre http://127.0.0.1:8000 en tu navegador")
    print("  Ctrl+C para detener\n")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
