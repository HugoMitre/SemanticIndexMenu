"""
menu_similarity.py
==================
Representación vectorial de etiquetas de navegación y cálculo del coseno de
similitud entre cada submenú y su menú padre.

Modelo por defecto
------------------
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
    · 12 capas, 384 dimensiones, ~118 M parámetros (mini-LLM real, corre en CPU)
    · Entrenado con destilación multilingüe sobre 50+ idiomas, español incluido
    · Su corpus de entrenamiento son PARÁFRASIS de frases cortas, que es
      exactamente la naturaleza de una etiqueta de menú ("Trámites y servicios",
      "Avisos de privacidad"). Los modelos entrenados para recuperación de
      documentos largos rinden peor en cadenas de 2–4 tokens.

Alternativas contrastables (usa --modelo para cambiarlas):
    · intfloat/multilingual-e5-small        (384-d; requiere prefijos "query: ")
    · hiiamsid/sentence_similarity_spanish_es (768-d; solo español, STS-es)
    · sentence-transformers/distiluse-base-multilingual-cased-v1 (512-d, línea base)

Enriquecimiento contextual
--------------------------
Una etiqueta aislada como "Padrón" es semánticamente ambigua. Antes de vectorizar
se construye un pseudo-documento con (a) una plantilla de dominio, (b) la
etiqueta y (c) los tokens informativos del slug de la URL. Esto reduce de forma
notable la varianza del coseno en etiquetas de 1–2 palabras.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional, Protocol, Sequence
from urllib.parse import urlparse

import numpy as np

MODELO_POR_DEFECTO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

MODELOS_SUGERIDOS = {
    "minilm":   "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "e5":       "intfloat/multilingual-e5-small",
    "es-sts":   "hiiamsid/sentence_similarity_spanish_es",
    "distiluse": "sentence-transformers/distiluse-base-multilingual-cased-v1",
}

# La plantilla se mantiene DELIBERADAMENTE CORTA: con mean-pooling, todo token
# compartido por igual entre etiquetas colapsa los vectores hacia una dirección
# común y comprime el rango del coseno. El grueso de la señal discriminativa
# debe venir de {etiqueta} y de {pistas} (tokens del slug, distintos por ítem).
PLANTILLA_CONTEXTO = "{etiqueta} — portal de gobierno de México. {pistas}"

_STOP_SLUG = {
    "index", "html", "php", "aspx", "www", "gob", "mx", "com", "org",
    "es", "default", "home", "page", "id", "cms", "portal", "site", "web",
}
_RE_SLUG = re.compile(r"[^a-záéíóúñü0-9]+", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Backends de embedding
# --------------------------------------------------------------------------- #

class Embedder(Protocol):
    """Contrato mínimo: texto -> matriz (n, d) de vectores L2-normalizados."""
    nombre: str
    def encode(self, textos: Sequence[str]) -> np.ndarray: ...


class SentenceTransformerEmbedder:
    """Envoltura sobre sentence-transformers."""

    def __init__(self, modelo: str = MODELO_POR_DEFECTO, device: Optional[str] = None,
                 batch_size: int = 64):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Falta sentence-transformers. Instálalo con:\n"
                "    pip install sentence-transformers"
            ) from exc
        self.nombre = MODELOS_SUGERIDOS.get(modelo, modelo)
        self.batch_size = batch_size
        self._e5 = "e5" in self.nombre.lower()
        self._modelo = SentenceTransformer(self.nombre, device=device)

    def encode(self, textos: Sequence[str]) -> np.ndarray:
        entrada = [f"query: {t}" for t in textos] if self._e5 else list(textos)
        vec = self._modelo.encode(
            entrada,
            batch_size=self.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,   # coseno == producto punto
            show_progress_bar=False,
        )
        return np.asarray(vec, dtype=np.float32)


class HashingEmbedder:
    """
    Embedder offline determinista (trigramas de caracteres con hashing trick).
    NO es semántico: existe solo para pruebas unitarias y para ejecutar el
    pipeline completo sin descargar pesos. Nunca lo uses para reportar resultados.
    """

    def __init__(self, dim: int = 256):
        self.nombre = "hashing-trigram (dry-run, NO semántico)"
        self.dim = dim

    def encode(self, textos: Sequence[str]) -> np.ndarray:
        M = np.zeros((len(textos), self.dim), dtype=np.float32)
        for i, t in enumerate(textos):
            t = f"  {t.lower()}  "
            for j in range(len(t) - 2):
                h = hashlib.md5(t[j:j + 3].encode("utf-8")).digest()
                M[i, int.from_bytes(h[:4], "big") % self.dim] += 1.0
        normas = np.linalg.norm(M, axis=1, keepdims=True)
        return M / np.clip(normas, 1e-9, None)


def crear_embedder(modelo: str = MODELO_POR_DEFECTO, dry_run: bool = False,
                   device: Optional[str] = None) -> Embedder:
    return HashingEmbedder() if dry_run else SentenceTransformerEmbedder(modelo, device)


# --------------------------------------------------------------------------- #
# Preparación del texto a vectorizar
# --------------------------------------------------------------------------- #

def _tokens_url(url: Optional[str]) -> list[str]:
    if not url:
        return []
    partes = _RE_SLUG.split(urlparse(url).path)
    return [p for p in partes if len(p) > 2 and p.lower() not in _STOP_SLUG]


def pistas_de_url(url: Optional[str], url_padre: Optional[str] = None,
                  heredadas: bool = True, max_tokens: int = 6) -> str:
    """
    Tokens informativos del path, para desambiguar etiquetas cortas.

    FUGA ESTRUCTURAL: los hermanos de un menú suelen compartir prefijo de ruta
    (/tramites/licencias/, /tramites/citas/). Incluir ese prefijo los hace
    parecerse por razones de arquitectura de URLs, no de semántica, e infla
    artificialmente la cohesión (baja V) y la similitud padre-hijo (sube D).
    Con `heredadas=False` se descartan los tokens que el nodo hereda de su padre
    y solo queda el segmento propio, que es la señal desambiguadora legítima.
    """
    tokens = _tokens_url(url)
    if not heredadas and url_padre:
        del_padre = {t.lower() for t in _tokens_url(url_padre)}
        tokens = [t for t in tokens if t.lower() not in del_padre]
    return " ".join(tokens[:max_tokens])


def construir_texto(etiqueta: str, url: Optional[str] = None,
                    modo_pistas: str = "sin-herencia",
                    url_padre: Optional[str] = None) -> str:
    """
    modo_pistas:
      "sin-herencia" (recomendado) etiqueta + tokens propios del slug
      "completas"                  etiqueta + slug íntegro (con fuga estructural)
      "ninguna"                    etiqueta cruda
    """
    if modo_pistas == "ninguna":
        return etiqueta
    pistas = pistas_de_url(url, url_padre, heredadas=(modo_pistas == "completas"))
    pistas = f"Ruta: {pistas}." if pistas else ""
    return PLANTILLA_CONTEXTO.format(etiqueta=etiqueta, pistas=pistas).strip()


# --------------------------------------------------------------------------- #
# Aplanado del árbol y cálculo de similitudes
# --------------------------------------------------------------------------- #

@dataclass
class Par:
    """Una relación padre-hijo con su coseno de similitud."""
    menu_id: str
    menu: str
    submenu_id: str
    submenu: str
    nivel: int
    url_submenu: Optional[str]
    coseno: float                      # cos(padre, hijo)
    mejor_padre_id: str                # menú raíz más afín al submenú
    mejor_padre: str
    coseno_mejor_padre: float


def _recorrer(nodo: dict, ruta: list[dict], acc: list[tuple[dict, dict]]) -> None:
    for hijo in nodo.get("submenus", []):
        acc.append((nodo, hijo))
        _recorrer(hijo, ruta + [nodo], acc)


def pares_padre_hijo(datos: dict) -> list[tuple[dict, dict]]:
    """Lista de (padre, hijo) para todas las aristas del árbol."""
    acc: list[tuple[dict, dict]] = []
    for menu in datos["menus"]:
        _recorrer(menu, [], acc)
    return acc


def calcular_similitudes(datos: dict, embedder: Embedder,
                         modo_pistas: str = "sin-herencia") -> tuple[list[Par], dict]:
    """
    Devuelve (lista de pares con coseno, artefactos).

    `artefactos` contiene la matriz de similitud completa y los índices, que el
    módulo de complejidad reutiliza para la cohesión entre hermanos y para la
    calibración contra la distribución nula.
    """
    aristas = pares_padre_hijo(datos)
    if not aristas:
        raise ValueError("El árbol no tiene relaciones padre-hijo que comparar.")

    # Inventario único de nodos (por id).
    nodos: dict[str, dict] = {}
    for menu in datos["menus"]:
        nodos[menu["id"]] = menu
        for d in _descendientes(menu):
            nodos[d["id"]] = d

    # Mapa hijo -> padre, necesario para descartar los tokens heredados.
    padre_de = {h["id"]: p["id"] for p, h in aristas}

    ids = list(nodos)
    idx = {nid: i for i, nid in enumerate(ids)}
    textos = []
    for i in ids:
        pid = padre_de.get(i)
        textos.append(construir_texto(
            nodos[i]["titulo"], nodos[i].get("url"), modo_pistas,
            url_padre=nodos[pid].get("url") if pid else None))

    V = embedder.encode(textos)                 # (n, d) ya L2-normalizado
    S = np.clip(V @ V.T, -1.0, 1.0)             # coseno de similitud

    ids_raiz = [m["id"] for m in datos["menus"]]

    pares: list[Par] = []
    for padre, hijo in aristas:
        ip, ih = idx[padre["id"]], idx[hijo["id"]]
        # ¿Bajo qué menú de primer nivel encajaría mejor este submenú?
        cand = [(S[ih, idx[r]], r) for r in ids_raiz]
        s_best, id_best = max(cand, key=lambda t: t[0])
        pares.append(Par(
            menu_id=padre["id"], menu=padre["titulo"],
            submenu_id=hijo["id"], submenu=hijo["titulo"],
            nivel=hijo["nivel"], url_submenu=hijo.get("url"),
            coseno=float(S[ip, ih]),
            mejor_padre_id=id_best, mejor_padre=nodos[id_best]["titulo"],
            coseno_mejor_padre=float(s_best),
        ))

    artefactos = {
        "S": S, "idx": idx, "ids": ids, "nodos": nodos,
        "ids_raiz": ids_raiz, "modelo": embedder.nombre,
        "contextualizado": modo_pistas,
    }
    return pares, artefactos


def _descendientes(nodo: dict):
    for h in nodo.get("submenus", []):
        yield h
        yield from _descendientes(h)
