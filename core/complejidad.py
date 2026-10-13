"""
menu_complexity.py
==================
Índice de Complejidad de Navegación (ICN) — formulación propuesta.

--------------------------------------------------------------------------------
0. Problema con el coseno crudo
--------------------------------------------------------------------------------
cos(·,·) de un encoder no es una escala interpretable: su rango efectivo depende
del modelo y del corpus (con MiniLM multilingüe, dos etiquetas administrativas
sin relación rara vez bajan de ~0.10, y dos idénticas rara vez pasan de ~0.90).
Reportar "0.2" sin calibrar no significa nada. Por eso se calibra contra la
DISTRIBUCIÓN NULA del propio sitio.

Sea P = {(M_i, S_ij)} el conjunto de aristas reales padre-hijo y
N = {(M_k, S_ij) : k != padre(S_ij)} el conjunto de emparejamientos falsos del
mismo sitio. Con mu_0 = media(cos sobre N) y sigma_0 = desv.est.(cos sobre N):

    ÍNDICE DE PERTENENCIA
        pi_ij = sigma( (s_ij - mu_0) / (tau * sigma_0) ),   sigma(z)=1/(1+e^-z)

    Interpretación calibrada y comparable entre sitios y modelos:
        pi = 0.50  el submenú se parece a su menú tanto como a uno al azar
        pi > 0.70  pertenencia clara
        pi < 0.30  el submenú está peor emparejado que un menú aleatorio
                   -> etiqueta mal ubicada
    Ejemplo del planteamiento: "Avisos de privacidad" bajo "Conoce Zacatecas"
    produce pi ~= 0.2 porque su coseno cae ~1.4 sigma por debajo del azar.

--------------------------------------------------------------------------------
1. Componentes del índice (todos en [0,1], todos "más alto = peor")
--------------------------------------------------------------------------------
Para el menú M_i con n_i hijos directos {S_i1..S_in}:

 (a) Desalineación semántica padre-hijo
        D_i = 1 - (1/n_i) * SUM_j pi_ij

 (b) Dispersión intra-menú (los hermanos no forman una categoría)
        V_i = 1 - (2 / (n_i(n_i-1))) * SUM_{j<k} pi(S_ij, S_ik)     [V_i = 0 si n_i < 2]

 (c) Amplitud / carga de decisión — ley de Hick-Hyman, T = a + b*log2(n+1)
        A_i = min(1, log2(1 + n_i) / log2(1 + n_ref))
     n_ref NO se fija a mano: es el percentil 95 de la amplitud observada en el
     corpus (o el maximo del propio sitio si se analiza uno solo). Ver
     amplitud_ref.py: n_ref y w_A estan confundidos mientras nada sature, asi
     que dejar n_ref libre le da al metodo un grado de libertad espurio.

 (d) Profundidad del subárbol (costo de navegación jerárquica)
        P_i = min(1, (d_i - 1) / (d_ref - 1)),           d_ref = 4

 (e) Tasa de migración: fracción de hijos que encajarían mejor en OTRO menú raíz
        X_i = (1/n_i) * SUM_j 1[ max_{k != i} s(S_ij, M_k) > s(S_ij, M_i) + delta ]

--------------------------------------------------------------------------------
2. Ecuación propuesta
--------------------------------------------------------------------------------
        ICN_i = w_D*D_i + w_V*V_i + w_X*X_i + w_A*A_i + w_P*P_i,
        con  SUM w = 1,  w >= 0   ==>   ICN_i pertenece a [0,1]

    Pesos por defecto (el bloque semántico domina con 0.75):
        w_D = 0.35, w_V = 0.20, w_X = 0.20, w_A = 0.15, w_P = 0.10

    Agregado del sitio, ponderado por tamaño del menú (evita que un menú de un
    solo elemento pese igual que uno de veinte):

        ICN_menus = SUM_i (n_i * ICN_i) / SUM_i n_i    (solo menús con n_i > 0)
        ICN_sitio = (1 - lambda) * ICN_menus + lambda * A_0,   lambda = 0.15
    donde A_0 = amplitud(numero de menus de primer nivel): la ley de Hick
    también se paga en la barra superior, no solo dentro de cada menú.

    Y su complemento reportable:  Coherencia = 1 - ICN_sitio.

--------------------------------------------------------------------------------
3. Lectura del ranking
--------------------------------------------------------------------------------
    · Ranking de submenús  : ordenar por pi ascendente  -> etiquetas a reubicar
    · Ranking de menús     : ordenar por ICN descendente -> secciones a rediseñar
    · Bandas sugeridas ICN : [0.00-0.25] simple · [0.25-0.45] aceptable
                             [0.45-0.65] confusa · [0.65-1.00] crítica
--------------------------------------------------------------------------------
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Iterable, Optional

import numpy as np

from .similitud import Par
from .amplitud_ref import resolver as resolver_n_ref

# --------------------------------------------------------------------------- #
# Parámetros del modelo de medición
# --------------------------------------------------------------------------- #

PESOS_POR_DEFECTO = {"D": 0.35, "V": 0.20, "X": 0.20, "A": 0.15, "P": 0.10}
TAU = 0.75          # temperatura de la logística de calibración
# ESPECIFICACIÓN de la amplitud de referencia (se resuelve en tiempo de
# ejecución): "max" | "pNN" | ruta a un JSON de corpus | entero.
# Ver amplitud_ref.py: fijarla a mano no es reproducible y además la deja
# confundida con w_A. El valor recomendado viene del corpus.
N_REF_SPEC = "max"
N_REF_FALLBACK = 25   # solo como defecto de amplitud() en pruebas unitarias
D_REF = 4           # profundidad de referencia
DELTA = 0.05        # margen mínimo de coseno para declarar "migración"
LAMBDA = 0.15       # peso de la amplitud del primer nivel en el agregado del sitio

BANDAS = [(0.25, "simple"), (0.45, "aceptable"), (0.65, "confusa"), (1.01, "crítica")]


def banda(icn: float) -> str:
    return next(nombre for umbral, nombre in BANDAS if icn < umbral)


def _sigmoide(z: np.ndarray | float) -> np.ndarray | float:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


# --------------------------------------------------------------------------- #
# Calibración
# --------------------------------------------------------------------------- #

@dataclass
class Calibrador:
    """Convierte coseno crudo -> índice de pertenencia pi en [0,1]."""
    metodo: str
    mu0: float
    sigma0: float
    tau: float = TAU
    lo: float = 0.0
    hi: float = 1.0
    n_nulo: int = 0
    muestra_nula: Optional[list] = None    # para el bootstrap del intervalo

    def __call__(self, s: float | np.ndarray) -> float | np.ndarray:
        if self.metodo == "nulo":
            return _sigmoide((np.asarray(s) - self.mu0) / (self.tau * self.sigma0))
        if self.metodo == "minmax":
            return np.clip((np.asarray(s) - self.lo) / max(self.hi - self.lo, 1e-9), 0, 1)
        return np.clip((np.asarray(s) + 1.0) / 2.0, 0, 1)   # "crudo"

    def resumen(self) -> dict:
        d = asdict(self)
        d.pop("muestra_nula", None)        # no se serializa: puede ser enorme
        return d


def ajustar_calibrador(artefactos: dict, pares: list[Par], metodo: str = "nulo",
                       tau: float = TAU, semilla: int = 7,
                       max_muestras: int = 20000) -> Calibrador:
    """
    Construye la distribución nula: cosenos entre submenús y menús raíz que NO
    son su padre. Es el punto de referencia "¿qué tan parecido sería al azar?".
    """
    S, idx = artefactos["S"], artefactos["idx"]
    ids_raiz = artefactos["ids_raiz"]

    nulos: list[float] = []
    for p in pares:
        for r in ids_raiz:
            if r != p.menu_id:
                nulos.append(float(S[idx[p.submenu_id], idx[r]]))

    rng = np.random.default_rng(semilla)
    arr = np.asarray(nulos, dtype=np.float64)
    if arr.size > max_muestras:
        arr = rng.choice(arr, size=max_muestras, replace=False)

    if arr.size < 2:                       # sitio con un solo menú raíz
        arr = np.asarray([p.coseno for p in pares], dtype=np.float64)

    mu0 = float(arr.mean())
    sigma0 = float(arr.std(ddof=1)) if arr.size > 1 else 1.0
    sigma0 = max(sigma0, 1e-3)

    todos = np.asarray([p.coseno for p in pares] + list(arr), dtype=np.float64)
    return Calibrador(
        metodo=metodo, mu0=mu0, sigma0=sigma0, tau=tau,
        lo=float(np.percentile(todos, 5)), hi=float(np.percentile(todos, 95)),
        n_nulo=int(arr.size), muestra_nula=arr.tolist(),
    )


def intervalo_pertenencia(pares: list[Par], cal: Calibrador, B: int = 600,
                          semilla: int = 11, min_nulos: int = 30,
                          min_pares: int = 8, max_ancho: float = 0.15) -> dict:
    """
    Precisión de la escala de pi, por bootstrap de la DISTRIBUCIÓN NULA.

    mu_0 y sigma_0 se estiman de una muestra finita de emparejamientos falsos.
    Un portal con 3 menús y 1 submenú aporta 2 pares nulos: con eso la escala de
    pi no está determinada, y su ICN no es comparable con el de un portal
    calibrado con 300.

    TRES CONDICIONES, no una
    ------------------------
    Un criterio basado solo en el ancho del intervalo tiene un agujero: cuando
    hay poquísimos pares nulos, sigma_0 colapsa cerca de cero, el argumento de
    la logística se dispara y pi satura en 0 o en 1 en TODAS las remuestras. El
    intervalo sale estrechísimo —no porque la estimación sea precisa, sino
    porque la función está saturada— y el portal pasaría el filtro.

    Por eso se exige:
      (1) al menos `min_nulos` pares nulos     -> sigma_0 estimable
      (2) al menos `min_pares` aristas         -> D, V y X no son ceros estructurales
      (3) intervalo no más ancho que `max_ancho`
      (4) la logística no saturada (mediana de pi fuera de los extremos)

    El umbral de 30 pares nulos viene de simulación: con 33 el intervalo mide
    ~0.155 y con 60 baja a ~0.115; por debajo de 30 deja de ser utilizable.
    """
    base = {"n_nulo": cal.n_nulo, "n_pares": len(pares), "ic_bajo": None,
            "ic_alto": None, "ancho": None, "pi_bootstrap": None,
            "precision_suficiente": None, "motivos": []}

    if not cal.muestra_nula or cal.metodo != "nulo" or not pares:
        return base

    rng = np.random.default_rng(semilla)
    nula = np.asarray(cal.muestra_nula, dtype=np.float64)
    cosenos = np.asarray([p.coseno for p in pares], dtype=np.float64)

    medias = np.empty(B)
    for b in range(B):
        m = rng.choice(nula, size=nula.size, replace=True)
        mu, sd = m.mean(), max(m.std(ddof=1) if m.size > 1 else 1.0, 1e-3)
        medias[b] = float(_sigmoide((cosenos - mu) / (cal.tau * sd)).mean())

    bajo, alto = (float(v) for v in np.percentile(medias, [2.5, 97.5]))
    mediana = float(np.median(medias))
    ancho = alto - bajo

    motivos = []
    if cal.n_nulo < min_nulos:
        par = "par nulo" if cal.n_nulo == 1 else "pares nulos"
        motivos.append(f"solo {cal.n_nulo} {par} (mínimo {min_nulos})")
    if len(pares) < min_pares:
        arista = "arista" if len(pares) == 1 else "aristas"
        motivos.append(f"solo {len(pares)} {arista} padre-hijo (mínimo {min_pares})")
    if ancho > max_ancho:
        motivos.append(f"intervalo de π de {ancho:.3f} de ancho (máximo {max_ancho})")
    if mediana > 0.97 or mediana < 0.03:
        motivos.append(f"π saturado en {mediana:.3f}: la escala no discrimina")

    base.update({
        "ic_bajo": round(bajo, 4), "ic_alto": round(alto, 4),
        "ancho": round(ancho, 4), "pi_bootstrap": round(mediana, 4),
        "precision_suficiente": not motivos, "motivos": motivos,
    })
    return base


# --------------------------------------------------------------------------- #
# Componentes
# --------------------------------------------------------------------------- #

def desalineacion(pis: Iterable[float]) -> float:
    """(a) D_i"""
    v = list(pis)
    return float(1.0 - np.mean(v)) if v else 0.0


def dispersion(ids_hijos: list[str], artefactos: dict, cal: Calibrador) -> float:
    """(b) V_i — cohesión entre hermanos."""
    if len(ids_hijos) < 2:
        return 0.0
    S, idx = artefactos["S"], artefactos["idx"]
    sims = [float(S[idx[a], idx[b]])
            for i, a in enumerate(ids_hijos) for b in ids_hijos[i + 1:]]
    return float(1.0 - np.mean(cal(np.asarray(sims))))


def amplitud(n: int, n_ref: int = N_REF_FALLBACK) -> float:
    """(c) A_i — ley de Hick-Hyman normalizada."""
    return float(min(1.0, math.log2(1 + max(n, 0)) / math.log2(1 + n_ref)))


def profundidad_norm(d: int, d_ref: int = D_REF) -> float:
    """(d) P_i"""
    return float(min(1.0, max(0, d - 1) / max(d_ref - 1, 1)))


def migracion(pares_hijos: list[Par], menu_id: str, delta: float = DELTA) -> float:
    """(e) X_i — fracción de hijos con mejor menú padre alternativo."""
    if not pares_hijos:
        return 0.0
    mal = sum(1 for p in pares_hijos
              if p.mejor_padre_id != menu_id
              and p.coseno_mejor_padre > p.coseno + delta)
    return mal / len(pares_hijos)


def _profundidad(nodo: dict) -> int:
    if not nodo.get("submenus"):
        return 1
    return 1 + max(_profundidad(h) for h in nodo["submenus"])


# --------------------------------------------------------------------------- #
# Cálculo integral
# --------------------------------------------------------------------------- #

@dataclass
class FilaSubmenu:
    menu: str
    submenu: str
    nivel: int
    coseno: float
    pertenencia: float
    mejor_padre: str
    coseno_mejor_padre: float
    migra: bool
    url: Optional[str]


@dataclass
class FilaMenu:
    menu_id: str
    menu: str
    n_hijos: int
    profundidad: int
    D: float
    V: float
    X: float
    A: float
    P: float
    ICN: float
    banda: str


def evaluar(datos: dict, pares: list[Par], artefactos: dict,
            pesos: Optional[dict] = None, metodo_calibracion: str = "nulo",
            tau: float = TAU, n_ref: str | int = N_REF_SPEC, d_ref: int = D_REF,
            delta: float = DELTA, lam: float = LAMBDA) -> dict:
    """Aplica la métrica completa y devuelve un reporte serializable."""
    pesos = dict(pesos or PESOS_POR_DEFECTO)
    total = sum(pesos.values())
    if not math.isclose(total, 1.0, abs_tol=1e-6):
        pesos = {k: v / total for k, v in pesos.items()}   # renormaliza

    n_ref_val, origen_n_ref = resolver_n_ref(n_ref, datos)

    cal = ajustar_calibrador(artefactos, pares, metodo_calibracion, tau)

    # --- nivel submenú -----------------------------------------------------
    filas_sub: list[FilaSubmenu] = []
    for p in pares:
        filas_sub.append(FilaSubmenu(
            menu=p.menu, submenu=p.submenu, nivel=p.nivel,
            coseno=round(p.coseno, 4),
            pertenencia=round(float(cal(p.coseno)), 4),
            mejor_padre=p.mejor_padre,
            coseno_mejor_padre=round(p.coseno_mejor_padre, 4),
            migra=bool(p.mejor_padre_id != p.menu_id
                       and p.coseno_mejor_padre > p.coseno + delta),
            url=p.url_submenu,
        ))

    # --- nivel menú raíz ---------------------------------------------------
    por_menu: dict[str, list[Par]] = {}
    for p in pares:
        por_menu.setdefault(p.menu_id, []).append(p)

    filas_menu: list[FilaMenu] = []
    for menu in datos["menus"]:
        mid = menu["id"]
        hijos_pares = [p for p in por_menu.get(mid, [])]
        ids_hijos = [h["id"] for h in menu.get("submenus", [])]
        n = len(ids_hijos)

        D = desalineacion([float(cal(p.coseno)) for p in hijos_pares
                           if p.submenu_id in ids_hijos])
        V = dispersion(ids_hijos, artefactos, cal)
        X = migracion([p for p in hijos_pares if p.submenu_id in ids_hijos], mid, delta)
        A = amplitud(n, n_ref_val)
        P = profundidad_norm(_profundidad(menu), d_ref)

        icn = pesos["D"] * D + pesos["V"] * V + pesos["X"] * X \
            + pesos["A"] * A + pesos["P"] * P

        filas_menu.append(FilaMenu(
            menu_id=mid, menu=menu["titulo"], n_hijos=n,
            profundidad=_profundidad(menu),
            D=round(D, 4), V=round(V, 4), X=round(X, 4),
            A=round(A, 4), P=round(P, 4),
            ICN=round(float(icn), 4), banda=banda(float(icn)),
        ))

    # --- nivel sitio -------------------------------------------------------
    # Los menús hoja (n_i = 0) no definen ninguna partición y se excluyen del
    # promedio; su costo se recoge en el término de amplitud del primer nivel.
    con_hijos = [f for f in filas_menu if f.n_hijos > 0]
    if con_hijos:
        pesos_n = np.array([f.n_hijos for f in con_hijos], dtype=float)
        icns = np.array([f.ICN for f in con_hijos], dtype=float)
        icn_menus = float((pesos_n * icns).sum() / pesos_n.sum())
    else:
        icn_menus = 0.0

    # Ley de Hick aplicada también a la barra de primer nivel.
    A0 = amplitud(len(filas_menu), n_ref_val)
    icn_sitio = (1 - lam) * icn_menus + lam * A0

    ic = intervalo_pertenencia(pares, cal)
    pis = np.array([f.pertenencia for f in filas_sub], dtype=float)

    return {
        "modelo": artefactos["modelo"],
        "contextualizado": artefactos["contextualizado"],
        "calibracion": cal.resumen(),
        "pesos": pesos,
        "parametros": {"tau": tau, "n_ref": n_ref_val, "d_ref": d_ref,
                       "delta": delta, "lambda": lam},
        "amplitud_referencia": origen_n_ref,
        "ICN_menus": round(icn_menus, 4),
        "amplitud_primer_nivel": round(A0, 4),
        "ICN_sitio": round(icn_sitio, 4),
        "coherencia_sitio": round(1 - icn_sitio, 4),
        "banda_sitio": banda(icn_sitio),
        "pertenencia_media": round(float(pis.mean()), 4) if pis.size else None,
        "precision": ic,
        "submenus_mal_ubicados": int((pis < 0.30).sum()),
        "tasa_migracion_global": round(
            sum(f.migra for f in filas_sub) / max(len(filas_sub), 1), 4),
        "menus": [asdict(f) for f in
                  sorted(filas_menu, key=lambda f: -f.ICN)],
        "submenus": [asdict(f) for f in
                     sorted(filas_sub, key=lambda f: f.pertenencia)],
    }
