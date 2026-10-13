"""
amplitud_ref.py
===============
Resolución de `n_ref`, la amplitud de referencia del componente A.

Por qué existe este módulo
--------------------------
En la primera versión `n_ref = 25` era una constante elegida a mano. Eso tiene
dos problemas:

1. **No es reproducible.** Nadie puede replicar el criterio porque no había
   ninguno: era un número redondo en un rango plausible.

2. **No es identificable.** Mientras ningún menú sature el tope, el término entra
   en el índice como

        w_A * A_i = [ w_A / log2(1 + n_ref) ] * log2(1 + n_i)

   de modo que solo el COCIENTE es identificable: n_ref = 9 con w_A = 0.15 es
   exactamente equivalente a n_ref = 25 con w_A = 0.212. Presentarlos como dos
   decisiones independientes le da al método una apariencia de fundamento que no
   tiene.

Solución adoptada
-----------------
`n_ref` se fija con el CORPUS, no por intuición:

    n_ref = percentil 95 de la amplitud observada en todos los portales del corpus

Así la saturación (A_i = 1) adquiere significado sustantivo: "este menú está
entre el 5% más ancho del país". Y el parámetro deja de ser un grado de libertad
del investigador para convertirse en una estadística del objeto de estudio.

Para un portal aislado, sin corpus disponible, `n_ref = max n_i` hace que A sea
explícitamente relativo al propio sitio; en ese caso el menú más ancho satura por
construcción y A se lee como "amplitud relativa al menú más ancho de este portal".

Definición de "amplitud observada"
----------------------------------
El número de hijos directos de cualquier nodo que tenga hijos, incluido el nodo
raíz virtual cuyos hijos son los menús de primer nivel. Incluir la raíz es
necesario para que A_0 (la amplitud de la barra superior) se compare contra la
misma distribución que los demás menús.

Uso
---
    # 1. Extraer todos los portales del corpus (cada uno deja su menus.json)
    # 2. Calcular n_ref del corpus
    python3 amplitud_ref.py corpus/*/menus.json -o corpus/n_ref.json
    # 3. Ejecutar el pipeline usando ese valor
    python3 run_pipeline.py <url> --menus-json ... --n-ref corpus/n_ref.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

PERCENTIL_POR_DEFECTO = 95.0


# --------------------------------------------------------------------------- #
# Amplitudes observadas en un árbol
# --------------------------------------------------------------------------- #

def amplitudes_de(datos: dict, incluir_raiz: bool = True) -> list[int]:
    """
    Número de hijos directos de cada nodo con hijos.

    Incluye el nodo raíz virtual (sus hijos son los menús de primer nivel), para
    que la amplitud de la barra superior se compare contra la misma distribución
    que la de los menús internos.
    """
    obs: list[int] = []
    if incluir_raiz and datos.get("menus"):
        obs.append(len(datos["menus"]))

    def recorrer(nodo: dict) -> None:
        hijos = nodo.get("submenus") or []
        if hijos:
            obs.append(len(hijos))
            for h in hijos:
                recorrer(h)

    for m in datos.get("menus", []):
        recorrer(m)
    return obs


# --------------------------------------------------------------------------- #
# Cálculo del n_ref del corpus
# --------------------------------------------------------------------------- #

def n_ref_de_corpus(rutas: list[Path], percentil: float = PERCENTIL_POR_DEFECTO,
                    incluir_raiz: bool = True) -> dict:
    """Agrupa las amplitudes de varios portales y devuelve el n_ref y su bitácora."""
    por_portal: dict[str, list[int]] = {}
    todas: list[int] = []

    for r in rutas:
        datos = json.loads(Path(r).read_text(encoding="utf-8"))
        obs = amplitudes_de(datos, incluir_raiz)
        if not obs:
            print(f"  [!] {r}: sin menús con hijos, se omite")
            continue
        clave = datos.get("sitio") or str(r)
        por_portal[clave] = obs
        todas.extend(obs)

    if not todas:
        raise SystemExit("Ningún portal aportó amplitudes observables.")

    arr = np.asarray(todas, dtype=float)
    # Techo, no redondeo al más cercano: n_ref es el MENOR entero que alcanza el
    # percentil pedido. Las amplitudes son enteros pequeños con muchos empates;
    # redondear a la baja haría saturar a todo el grupo empatado en ese valor
    # (con 13 observaciones, P95 = 5.4 -> redondeo 5 satura el 46%, techo 6
    # satura el 8%). El techo garantiza una tasa de saturación <= al complemento
    # del percentil, salvo empates exactos en el propio n_ref.
    n_ref = max(2, int(math.ceil(float(np.percentile(arr, percentil)))))

    satura = int((arr >= n_ref).sum())
    return {
        "n_ref": n_ref,
        "percentil": percentil,
        "percentil_crudo": round(float(np.percentile(arr, percentil)), 2),
        "redondeo": "techo",
        "incluye_raiz": incluir_raiz,
        "n_portales": len(por_portal),
        "n_observaciones": int(arr.size),
        "amplitud_min": int(arr.min()),
        "amplitud_mediana": float(np.median(arr)),
        "amplitud_media": round(float(arr.mean()), 2),
        "amplitud_max": int(arr.max()),
        "n_saturan": satura,
        "tasa_saturacion": round(satura / arr.size, 4),
        "portales": {k: sorted(v, reverse=True) for k, v in por_portal.items()},
    }


# --------------------------------------------------------------------------- #
# Resolución en tiempo de ejecución
# --------------------------------------------------------------------------- #

def resolver(spec: str | int | None, datos: dict,
             percentil: float = PERCENTIL_POR_DEFECTO) -> tuple[int, dict]:
    """
    Traduce la especificación de `--n-ref` a un entero.

    Formas admitidas
        "corpus:<ruta>" o "<ruta>.json" -> lee `n_ref` de un archivo de corpus
        "max"  (o "auto")               -> máxima amplitud de ESTE portal
        "p95", "p90", ...               -> percentil de ESTE portal
        entero                          -> valor literal (desaconsejado)
    """
    obs = amplitudes_de(datos)
    if not obs:
        obs = [2]

    if spec is None:
        spec = "max"
    spec = str(spec).strip()

    if spec.startswith("corpus:"):
        spec = spec.split(":", 1)[1]

    if spec.lower().endswith(".json"):
        meta = json.loads(Path(spec).read_text(encoding="utf-8"))
        n_ref = int(meta["n_ref"])
        origen = {
            "modo": "corpus",
            "archivo": spec,
            "percentil": meta.get("percentil"),
            "n_portales": meta.get("n_portales"),
            "n_observaciones": meta.get("n_observaciones"),
        }
    elif spec.lower() in ("max", "auto"):
        n_ref = int(max(obs))
        origen = {"modo": "max-sitio", "n_observaciones": len(obs)}
    elif spec.lower().startswith("p") and spec[1:].replace(".", "").isdigit():
        q = float(spec[1:])
        n_ref = max(2, int(math.ceil(float(np.percentile(np.asarray(obs, float), q)))))
        origen = {"modo": f"p{q:g}-sitio", "n_observaciones": len(obs)}
    else:
        try:
            n_ref = int(float(spec))
        except ValueError as exc:
            raise SystemExit(
                f"--n-ref no reconocido: '{spec}'. Usa un entero, 'max', "
                f"'p95' o la ruta a un JSON de corpus."
            ) from exc
        origen = {"modo": "literal",
                  "advertencia": "valor fijado a mano: no reproducible"}

    n_ref = max(int(n_ref), 2)
    satura = sum(1 for x in obs if x >= n_ref)
    origen.update({
        "n_ref": n_ref,
        "amplitudes_sitio": sorted(obs, reverse=True),
        "menus_que_saturan": satura,
        "tasa_saturacion_sitio": round(satura / len(obs), 4),
    })
    return n_ref, origen


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Calcula n_ref como percentil de la amplitud del corpus.")
    ap.add_argument("menus_json", nargs="+",
                    help="Rutas a los menus.json de los portales del corpus.")
    ap.add_argument("-o", "--output", default="corpus/n_ref.json")
    ap.add_argument("--percentil", type=float, default=PERCENTIL_POR_DEFECTO)
    ap.add_argument("--sin-raiz", action="store_true",
                    help="Excluye la amplitud del primer nivel del agregado.")
    args = ap.parse_args(argv)

    rutas = [Path(r) for r in args.menus_json]
    faltan = [r for r in rutas if not r.exists()]
    if faltan:
        raise SystemExit(f"No existen: {', '.join(map(str, faltan))}")

    meta = n_ref_de_corpus(rutas, args.percentil, not args.sin_raiz)

    salida = Path(args.output)
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                      encoding="utf-8")

    print(f"\nCorpus: {meta['n_portales']} portales · "
          f"{meta['n_observaciones']} amplitudes observadas")
    print(f"  min {meta['amplitud_min']} · mediana {meta['amplitud_mediana']:g} · "
          f"media {meta['amplitud_media']} · max {meta['amplitud_max']}")
    print(f"\n  n_ref = P{meta['percentil']:g} = {meta['n_ref']}")
    print(f"  saturan {meta['n_saturan']} de {meta['n_observaciones']} menús "
          f"({100 * meta['tasa_saturacion']:.1f}%)")
    print(f"\n-> {salida}")
    print(f"\nÚsalo con:  python3 run_pipeline.py <url> --n-ref {salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
