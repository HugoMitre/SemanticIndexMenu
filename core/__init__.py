"""Núcleo de cálculo del Índice de Complejidad de Navegación (ICN)."""
from .scraper import extraer_menus
from .similitud import calcular_similitudes, crear_embedder, MODELO_POR_DEFECTO
from .complejidad import evaluar, PESOS_POR_DEFECTO
from .amplitud_ref import n_ref_de_corpus, amplitudes_de

__all__ = ["extraer_menus", "calcular_similitudes", "crear_embedder",
           "MODELO_POR_DEFECTO", "evaluar", "PESOS_POR_DEFECTO",
           "n_ref_de_corpus", "amplitudes_de"]
