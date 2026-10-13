"""
core/estadisticos.py
====================
Estadística de corpus sobre los componentes del ICN.

Qué produce y por qué importa para una revista arbitrada:

DESCRIPTIVOS
    Media, desviación, cuartiles y rango de cada componente. Un componente con
    desviación casi nula no discrimina entre portales y no está aportando nada
    al índice; conviene detectarlo antes de que lo detecte un revisor.

CORRELACIÓN DE RANGOS ENTRE COMPONENTES (Spearman)
    Si dos componentes correlacionan por encima de ~0.9, están midiendo lo mismo
    y el índice los está contando dos veces. Es la prueba de redundancia.

CONTRIBUCIÓN AL ÍNDICE
    w_c * media(c) / media(ICN). Responde "¿qué parte del índice viene de cada
    componente?", que no es lo mismo que el peso nominal: un componente con peso
    0.35 pero valores bajos contribuye poco.

SENSIBILIDAD DE PESOS (Dirichlet + tau de Kendall)
    La crítica evidente al índice es que los pesos son arbitrarios. La respuesta
    empírica: muestrear w ~ Dirichlet(1,...,1) y medir cuánto cambia el ORDEN de
    los portales. Si tau de Kendall se mantiene alto, el ranking es robusto a la
    elección de pesos y esa objeción queda desactivada con datos.

CONSISTENCIA INTERNA
    Se reporta alfa de Cronbach con una advertencia explícita: el ICN es un
    índice FORMATIVO (los componentes definen el constructo), no reflectivo (no
    son manifestaciones de un factor latente). Para un índice formativo, una
    alfa baja NO es un defecto: los componentes deben ser complementarios, no
    intercambiables. Se incluye porque los revisores la piden, con la nota de
    por qué no es el criterio adecuado.
"""

from __future__ import annotations

import numpy as np

COMPONENTES = ["D", "V", "X", "A", "P"]
NOMBRES = {
    "D": "Desalineación",
    "V": "Dispersión",
    "X": "Migración",
    "A": "Amplitud",
    "P": "Profundidad",
}


# --------------------------------------------------------------------------- #
# Utilidades sin dependencias externas (no requiere scipy)
# --------------------------------------------------------------------------- #

def _rangos(x: np.ndarray) -> np.ndarray:
    """Rangos con promedio en empates."""
    orden = np.argsort(x, kind="mergesort")
    r = np.empty(len(x), dtype=float)
    r[orden] = np.arange(len(x), dtype=float)
    vals, inv, cuentas = np.unique(x, return_inverse=True, return_counts=True)
    for i, c in enumerate(cuentas):
        if c > 1:
            m = inv == i
            r[m] = r[m].mean()
    return r


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra, rb = _rangos(np.asarray(a, float)), _rangos(np.asarray(b, float))
    ra, rb = ra - ra.mean(), rb - rb.mean()
    d = np.sqrt((ra ** 2).sum() * (rb ** 2).sum())
    return float((ra * rb).sum() / d) if d > 1e-12 else float("nan")


def kendall_tau_b(a: np.ndarray, b: np.ndarray) -> float:
    """tau-b de Kendall, con corrección por empates. O(n^2), suficiente aquí."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    n = len(a)
    if n < 2:
        return float("nan")
    conc = disc = ea = eb = 0
    for i in range(n - 1):
        da, db = a[i + 1:] - a[i], b[i + 1:] - b[i]
        s = np.sign(da) * np.sign(db)
        conc += int((s > 0).sum())
        disc += int((s < 0).sum())
        ea += int(((da == 0) & (db != 0)).sum())
        eb += int(((db == 0) & (da != 0)).sum())
    emp = np.sqrt((conc + disc + ea) * (conc + disc + eb))
    return float((conc - disc) / emp) if emp > 0 else float("nan")


def _resumen(x: np.ndarray) -> dict:
    x = np.asarray(x, float)
    q1, med, q3 = np.percentile(x, [25, 50, 75])
    return {
        "n": int(x.size),
        "media": round(float(x.mean()), 4),
        "de": round(float(x.std(ddof=1)) if x.size > 1 else 0.0, 4),
        "min": round(float(x.min()), 4),
        "q1": round(float(q1), 4),
        "mediana": round(float(med), 4),
        "q3": round(float(q3), 4),
        "max": round(float(x.max()), 4),
        "rango_iq": round(float(q3 - q1), 4),
    }


# --------------------------------------------------------------------------- #
# Análisis principal
# --------------------------------------------------------------------------- #

def analizar_corpus(portales: list[dict], pesos: dict, lam: float = 0.15,
                    n_muestras: int = 2000, semilla: int = 7) -> dict:
    """
    `portales`: lista de dicts con clave, nombre, ICN, D, V, X, A, P, pi_media,
    n_menus, n_submenus, y opcionalmente `pi_todos` (lista de pi por submenú).
    """
    if len(portales) < 2:
        return {"suficiente": False,
                "nota": "Se necesitan al menos dos portales para estadística de corpus."}

    M = {c: np.array([p[c] for p in portales], float) for c in COMPONENTES}
    icn = np.array([p["ICN"] for p in portales], float)

    # ---- descriptivos --------------------------------------------------
    desc = {c: {**_resumen(M[c]), "nombre": NOMBRES[c], "peso": pesos[c]}
            for c in COMPONENTES}
    desc["ICN"] = {**_resumen(icn), "nombre": "Índice de complejidad", "peso": 1.0}

    # ---- correlación de rangos ----------------------------------------
    etiquetas = COMPONENTES + ["ICN"]
    series = [M[c] for c in COMPONENTES] + [icn]
    R = [[round(1.0 if i == j else spearman(series[i], series[j]), 3)
          for j in range(len(series))] for i in range(len(series))]
    redundantes = [
        {"a": etiquetas[i], "b": etiquetas[j], "rho": R[i][j]}
        for i in range(len(COMPONENTES)) for j in range(i + 1, len(COMPONENTES))
        if abs(R[i][j]) >= 0.90
    ]

    # ---- contribución al índice ---------------------------------------
    media_icn = float(icn.mean())
    contrib = {}
    for c in COMPONENTES:
        aporte = pesos[c] * float(M[c].mean())
        contrib[c] = {
            "nombre": NOMBRES[c],
            "peso": pesos[c],
            "media_componente": round(float(M[c].mean()), 4),
            "aporte": round(aporte, 4),
            "porcentaje": round(100 * aporte / media_icn, 1) if media_icn > 0 else 0.0,
        }

    # ---- componentes sin varianza --------------------------------------
    constantes = componentes_constantes(M, icn, pesos)

    # ---- sensibilidad de pesos ----------------------------------------
    sens = sensibilidad_pesos(M, icn, portales, lam, n_muestras, semilla)

    # ---- consistencia interna -----------------------------------------
    alfa = cronbach(np.column_stack([M[c] for c in COMPONENTES]))

    # ---- cuartiles del ICN y percentil por portal ----------------------
    q1, med, q3 = (round(float(v), 4) for v in np.percentile(icn, [25, 50, 75]))
    for p in portales:
        p["percentil_ICN"] = round(float((icn <= p["ICN"]).sum() / len(icn) * 100), 1)
        p["cuartil"] = int(np.searchsorted([q1, med, q3], p["ICN"]) + 1)

    # ---- distribución de pi agrupada -----------------------------------
    pis = np.array([v for p in portales for v in p.get("pi_todos", [])], float)
    dist_pi = None
    if pis.size:
        bordes = np.linspace(0, 1, 21)
        conteo, _ = np.histogram(pis, bins=bordes)
        dist_pi = {
            "n_pares": int(pis.size),
            "media": round(float(pis.mean()), 4),
            "mediana": round(float(np.median(pis)), 4),
            "bajo_030": int((pis < 0.30).sum()),
            "prop_bajo_030": round(float((pis < 0.30).mean()), 4),
            "bajo_050": int((pis < 0.50).sum()),
            "prop_bajo_050": round(float((pis < 0.50).mean()), 4),
            "bordes": [round(float(b), 3) for b in bordes],
            "conteo": [int(c) for c in conteo],
        }

    return {
        "suficiente": True,
        "n_portales": len(portales),
        "descriptivos": desc,
        "correlacion": {"etiquetas": etiquetas, "matriz": R,
                        "redundantes": redundantes},
        "contribucion": contrib,
        "constantes": constantes,
        "sensibilidad": sens,
        "consistencia": alfa,
        "cuartiles": {"q1": q1, "mediana": med, "q3": q3,
                      "min": round(float(icn.min()), 4),
                      "max": round(float(icn.max()), 4)},
        "distribucion_pi": dist_pi,
    }


def componentes_constantes(M: dict, icn: np.ndarray, pesos: dict,
                           umbral: float = 0.005) -> dict:
    """
    Detecta componentes cuya varianza en el corpus es prácticamente nula y
    calcula qué pasaría si se eliminaran.

    Un componente constante no es inofensivo. No discrimina entre portales, pero
    sí consume peso: aporta `w_c * valor` idéntico a todos los índices, de modo
    que comprime el rango del ICN hacia el centro y hace que las diferencias
    reales parezcan menores de lo que son. En la práctica el índice tiene menos
    dimensiones efectivas de las que declara.

    Dos causas posibles, y conviene distinguirlas antes de actuar:
      (a) el constructo realmente no varía en este corpus, o
      (b) la extracción no está capturando esa dimensión —por ejemplo, si todos
          los portales salen con profundidad 2 porque el scraper no baja más—,
          en cuyo caso el problema es de medición y no del índice.
    """
    planos = [c for c in COMPONENTES if float(M[c].std(ddof=1)) < umbral]
    if not planos:
        return {"hay": False, "componentes": []}

    vivos = [c for c in COMPONENTES if c not in planos]
    detalle = []
    for c in planos:
        v = float(M[c].mean())
        detalle.append({
            "clave": c, "nombre": NOMBRES[c], "peso": pesos[c],
            "valor_constante": round(v, 4),
            "aporte_fijo": round(pesos[c] * v, 4),
            "pct_del_icn": round(100 * pesos[c] * v / float(icn.mean()), 1)
                           if icn.mean() > 0 else 0.0,
        })

    resultado = {
        "hay": True,
        "componentes": detalle,
        "peso_desperdiciado": round(sum(pesos[c] for c in planos), 3),
        "aporte_fijo_total": round(sum(d["aporte_fijo"] for d in detalle), 4),
        "dimensiones_efectivas": len(vivos),
    }

    # ¿Cambiaría el orden si se eliminaran y se renormalizaran los pesos?
    if vivos and len(icn) > 1:
        tot = sum(pesos[c] for c in vivos)
        icn_sin = sum((pesos[c] / tot) * M[c] for c in vivos)
        resultado.update({
            "rho_con_sin": round(spearman(icn, icn_sin), 4),
            "tau_con_sin": round(kendall_tau_b(icn, icn_sin), 4),
            "icn_sin_constantes": [round(float(v), 4) for v in icn_sin],
            "rango_sin": [round(float(icn_sin.min()), 4),
                          round(float(icn_sin.max()), 4)],
            "rango_con": [round(float(icn.min()), 4), round(float(icn.max()), 4)],
        })
    return resultado


def sensibilidad_pesos(M: dict, icn_base: np.ndarray, portales: list[dict],
                       lam: float, n_muestras: int, semilla: int) -> dict:
    """
    Remuestrea los pesos desde una Dirichlet(1,...,1) —uniforme sobre el símplex—
    y mide cuánto se conserva el orden de los portales.

    Interpretación: si tau de Kendall se mantiene alto, el ranking no depende de
    la elección particular de pesos, y la objeción "los pesos son arbitrarios"
    deja de ser una amenaza a la validez del ordenamiento.
    """
    rng = np.random.default_rng(semilla)
    X = np.column_stack([M[c] for c in COMPONENTES])          # (n_portales, 5)
    A0 = np.array([p.get("A0", 0.0) for p in portales], float)

    orden_base = np.argsort(icn_base)
    taus = np.empty(n_muestras)
    peor_base = int(np.argmax(icn_base))
    conserva_peor = 0

    for k in range(n_muestras):
        w = rng.dirichlet(np.ones(len(COMPONENTES)))
        icn_menus = X @ w
        icn_k = (1 - lam) * icn_menus + lam * A0
        taus[k] = kendall_tau_b(icn_base, icn_k)
        if int(np.argmax(icn_k)) == peor_base:
            conserva_peor += 1

    taus = taus[~np.isnan(taus)]
    return {
        "n_muestras": int(taus.size),
        "tau_media": round(float(taus.mean()), 4),
        "tau_de": round(float(taus.std(ddof=1)), 4) if taus.size > 1 else 0.0,
        "tau_p05": round(float(np.percentile(taus, 5)), 4),
        "tau_p50": round(float(np.percentile(taus, 50)), 4),
        "tau_min": round(float(taus.min()), 4),
        "prop_tau_mayor_08": round(float((taus >= 0.8).mean()), 4),
        "conserva_mas_complejo": round(conserva_peor / max(len(taus), 1), 4),
        "orden_base": [portales[i]["clave"] for i in orden_base],
        "lectura": _leer_sensibilidad(float(taus.mean())),
    }


def _leer_sensibilidad(tau: float) -> str:
    if tau >= 0.85:
        return ("El ordenamiento es robusto: cambiar los pesos dentro de todo el "
                "símplex casi no lo altera, así que el ranking no es un artefacto "
                "de la ponderación elegida.")
    if tau >= 0.65:
        return ("El ordenamiento es moderadamente estable. Conviene reportar el "
                "rango de posiciones de cada portal, no solo su posición puntual.")
    return ("El ordenamiento depende fuertemente de los pesos. No debe "
            "presentarse el ranking como un resultado sin declarar esta "
            "dependencia y justificar la ponderación elegida.")


def cronbach(X: np.ndarray) -> dict:
    """Alfa de Cronbach, con la advertencia sobre índices formativos."""
    n, k = X.shape
    if n < 2 or k < 2:
        return {"alfa": None, "nota": "Datos insuficientes."}
    var_items = X.var(axis=0, ddof=1).sum()
    var_total = X.sum(axis=1).var(ddof=1)
    alfa = (k / (k - 1)) * (1 - var_items / var_total) if var_total > 0 else float("nan")
    return {
        "alfa": round(float(alfa), 4) if np.isfinite(alfa) else None,
        "k_componentes": int(k),
        "nota": ("El ICN es un índice FORMATIVO: los componentes definen el "
                 "constructo en lugar de reflejar un factor latente. Una alfa "
                 "baja no es un defecto aquí; indica que los componentes son "
                 "complementarios y no intercambiables, que es justamente lo "
                 "que se busca. Se reporta por convención, no como criterio "
                 "de validez."),
    }
