"""
menu_scraper.py
===============
Extracción del árbol de navegación (menús -> submenús -> ...) de un portal web.

Diseñado para portales de gobierno de México (WordPress, Bootstrap, Drupal,
plantillas gob.mx), que suelen usar uno de estos patrones:

    (A) WordPress clásico   <ul class="menu"><li class="menu-item-has-children">
                              <a>..</a><ul class="sub-menu">...</ul></li></ul>
    (B) Bootstrap dropdown  <li class="dropdown"><a>..</a>
                              <div class="dropdown-menu"><a>..</a><a>..</a></div></li>
    (C) HTML5 nativo        <details><summary>..</summary><ul>...</ul></details>

Uso rápido
----------
    python menu_scraper.py https://www.aguascalientes.gob.mx/ -o data/menus.json
    python menu_scraper.py https://www.aguascalientes.gob.mx/ --render -o data/menus.json

Nota ética/legal: el script verifica robots.txt por defecto. Muchos portales
estatales lo restringen; para investigación académica solicita autorización al
área de gobierno digital antes de desactivar la verificación (--ignore-robots).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup, Tag

# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #

USER_AGENT = (
    "MenuComplexityBot/1.0 (investigacion academica; "
    "+contacto: tu-correo@institucion.mx) requests/2.x"
)

# Texto que aparece en menús pero no es una etiqueta semántica real.
RUIDO_ETIQUETAS = {
    "", "-", "|", "»", "«", "+", "···", "...",
    "menu", "menú", "abrir menú", "cerrar menú", "toggle navigation",
    "buscar", "search", "ir al contenido", "saltar al contenido",
    "skip to content", "inicio de sesión", "iniciar sesión", "login",
}

# Contenedores candidatos a ser la navegación principal.
SELECTORES_NAV = [
    "nav",
    "[role='navigation']",
    "header",
    ".navbar",
    ".main-navigation",
    ".menu-principal",
    ".main-menu",
    "#menu",
    "#nav",
    "#menu-principal",
    "ul.menu",
    "ul.navbar-nav",
    "ul.nav",
]

RE_ESPACIOS = re.compile(r"\s+")
RE_INVISIBLES = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")


# --------------------------------------------------------------------------- #
# Estructura de datos
# --------------------------------------------------------------------------- #

@dataclass
class NodoMenu:
    """Un nodo del árbol de navegación."""
    id: str
    titulo: str
    url: Optional[str]
    nivel: int
    submenus: list["NodoMenu"] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["submenus"] = [s.to_dict() for s in self.submenus]
        return d

    def hojas(self) -> Iterable["NodoMenu"]:
        if not self.submenus:
            yield self
        for s in self.submenus:
            yield from s.hojas()

    def descendientes(self) -> Iterable["NodoMenu"]:
        for s in self.submenus:
            yield s
            yield from s.descendientes()

    def profundidad(self) -> int:
        """Profundidad máxima del subárbol (el nodo solo = 1)."""
        if not self.submenus:
            return 1
        return 1 + max(s.profundidad() for s in self.submenus)


# --------------------------------------------------------------------------- #
# Utilidades de texto y URL
# --------------------------------------------------------------------------- #

def limpiar_texto(texto: str) -> str:
    """Normaliza espacios y elimina caracteres invisibles. Conserva acentos."""
    texto = RE_INVISIBLES.sub("", texto or "")
    texto = RE_ESPACIOS.sub(" ", texto).strip()
    return texto


def es_etiqueta_util(texto: str) -> bool:
    t = texto.strip().lower()
    if t in RUIDO_ETIQUETAS:
        return False
    # Etiquetas larguísimas suelen ser párrafos de contenido, no menús.
    return 1 <= len(texto) <= 90


def absolutizar(href: Optional[str], base: str) -> Optional[str]:
    if not href:
        return None
    href = href.strip()
    if href.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    url, _ = urldefrag(urljoin(base, href))
    return url or None


def robots_permite(url: str, user_agent: str = USER_AGENT, timeout: int = 10) -> bool:
    """Consulta robots.txt del dominio. Ante error de red, devuelve True."""
    p = urlparse(url)
    robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
    rp = RobotFileParser()
    try:
        resp = requests.get(robots_url, timeout=timeout,
                            headers={"User-Agent": user_agent})
        if resp.status_code >= 400:
            return True
        rp.parse(resp.text.splitlines())
        return rp.can_fetch(user_agent, url)
    except requests.RequestException:
        return True


# --------------------------------------------------------------------------- #
# Descarga del HTML
# --------------------------------------------------------------------------- #

def obtener_html(url: str, render_js: bool = False, timeout: int = 30,
                 espera_ms: int = 2500) -> str:
    """
    Devuelve el HTML de `url`.

    - render_js=False -> requests (rápido; sirve si el menú viene en el HTML).
    - render_js=True  -> Playwright (necesario si el menú lo pinta JavaScript,
                         caso frecuente en portales estatales recientes).
    - Si `url` apunta a un archivo local, lo lee del disco (útil para pruebas
      reproducibles y para trabajar sobre un snapshot archivado).
    """
    ruta = Path(url)
    if url.startswith("file://") or (ruta.exists() and ruta.is_file()):
        p = Path(url.replace("file://", "")) if url.startswith("file://") else ruta
        return p.read_text(encoding="utf-8", errors="ignore")

    # Si no parece URL y tampoco existe en disco, el fallo es de ruta, no de red.
    if "://" not in url:
        raise FileNotFoundError(
            f"'{url}' no es una URL (le falta http:// o https://) y no existe "
            f"como archivo.\nDirectorio actual: {Path.cwd()}\n"
            "Ejecuta el script desde la raíz del proyecto, o pasa una URL completa."
        )

    if render_js:
        return _obtener_html_playwright(url, timeout, espera_ms)

    resp = requests.get(
        url, timeout=timeout,
        headers={"User-Agent": USER_AGENT,
                 "Accept-Language": "es-MX,es;q=0.9,en;q=0.5"},
    )
    resp.raise_for_status()
    resp.encoding = resp.encoding or resp.apparent_encoding
    return resp.text


def _obtener_html_playwright(url: str, timeout: int, espera_ms: int) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Falta Playwright. Instálalo con:\n"
            "    pip install playwright && playwright install chromium"
        ) from exc

    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=True)
        pagina = navegador.new_page(user_agent=USER_AGENT, locale="es-MX")
        pagina.goto(url, timeout=timeout * 1000, wait_until="networkidle")
        # Muchos menús solo inyectan el submenú al hacer hover; forzamos el
        # renderizado desplegando todo vía CSS antes de leer el DOM.
        pagina.add_style_tag(content="""
            .sub-menu, .dropdown-menu, .submenu, ul ul {
                display: block !important;
                visibility: visible !important;
                opacity: 1 !important;
            }
        """)
        pagina.wait_for_timeout(espera_ms)
        html = pagina.content()
        navegador.close()
    return html


# --------------------------------------------------------------------------- #
# Localización del contenedor de navegación
# --------------------------------------------------------------------------- #

def _puntuar_contenedor(nodo: Tag) -> float:
    """
    Heurística: un menú real tiene muchos <li> anidados y enlaces cortos.
    Se premia el anidamiento (evidencia de submenús) y se castiga el texto largo
    (indicio de que es una lista de contenido, no de navegación).
    """
    enlaces = nodo.find_all("a", href=True)
    if not enlaces:
        return 0.0
    con_hijos = [li for li in nodo.find_all("li") if li.find(["ul", "ol"])]
    largos = [a for a in enlaces if len(limpiar_texto(a.get_text())) > 60]
    return 3.0 * len(con_hijos) + 1.0 * len(enlaces) - 1.5 * len(largos)


def localizar_nav(soup: BeautifulSoup, selector: Optional[str] = None) -> Optional[Tag]:
    """Devuelve el contenedor con mayor probabilidad de ser el menú principal."""
    if selector:
        return soup.select_one(selector)

    candidatos: list[Tag] = []
    for sel in SELECTORES_NAV:
        candidatos.extend(soup.select(sel))
    # También listas anidadas sueltas (menús sin <nav>).
    candidatos.extend(ul for ul in soup.find_all("ul") if ul.find("ul"))

    if not candidatos:
        return None
    return max(candidatos, key=_puntuar_contenedor)


# --------------------------------------------------------------------------- #
# Parseo recursivo del árbol
# --------------------------------------------------------------------------- #

def _ancla_propia(li: Tag) -> Optional[Tag]:
    """
    Primer <a> del <li> que NO pertenezca a un submenú anidado.

    Un ancla es "propia" si al ascender desde ella hasta el <li> no se cruza
    ninguna lista anidada ni un contenedor .dropdown-menu.
    """
    for a in li.find_all("a", href=True):
        actual, propia = a.parent, True
        while actual is not None and actual is not li:
            clases = actual.get("class") or []
            if actual.name in ("ul", "ol") or "dropdown-menu" in clases:
                propia = False
                break
            actual = actual.parent
        if propia:
            return a
    return None


def _raiz(nodo: Tag) -> Tag:
    actual = nodo
    while actual.parent is not None:
        actual = actual.parent
    return actual


# Atributos con los que un rótulo apunta a su panel de submenú.
ATTRS_PANEL = ("aria-controls", "data-bs-target", "data-target", "data-menu-target")


def _panel_referenciado(li: Tag) -> list[Tag]:
    """
    Mega-menús: el panel de submenú NO está anidado en el <li>, sino en otra
    parte del documento, referenciado por aria-controls / data-target / href="#id".
    Resuelve esa referencia y devuelve el panel como contenedor de submenú.
    """
    disparadores = [li] + li.find_all(["a", "button", "summary"], recursive=True)[:2]
    ids: list[str] = []
    for d in disparadores:
        for attr in ATTRS_PANEL:
            v = (d.get(attr) or "").strip()
            if v:
                ids.append(v.lstrip("#"))
        href = (d.get("href") or "").strip()
        if href.startswith("#") and len(href) > 1:
            ids.append(href[1:])

    raiz, paneles = _raiz(li), []
    for pid in dict.fromkeys(ids):
        panel = raiz.find(id=pid)
        # Descarta autorreferencias y ancestros (romperían la recursión).
        if panel is None or panel is li or panel in li.parents or li in panel.descendants:
            continue
        if panel.find("a", href=True):
            paneles.append(panel)
    return paneles


def _sublistas(li: Tag) -> list[Tag]:
    """Contenedores de submenú colgando de un <li> (patrones A, B y C)."""
    subs: list[Tag] = []
    for hijo in li.find_all(["ul", "ol", "div", "details"], recursive=False):
        if hijo.name in ("ul", "ol"):
            subs.append(hijo)
        elif hijo.name == "details":
            subs.extend(hijo.find_all(["ul", "ol"], recursive=False))
        else:  # <div class="dropdown-menu"> ...
            anidadas = hijo.find_all(["ul", "ol"], recursive=False)
            if anidadas:
                subs.extend(anidadas)
            elif hijo.find("a", href=True):
                subs.append(hijo)  # div con <a> sueltos (Bootstrap)
    if not subs:
        subs.extend(_panel_referenciado(li))
    return subs


def _items_de(contenedor: Tag) -> list[Tag]:
    """
    Elementos de un contenedor, en orden de preferencia:
      1. <li> directos            (lista clásica)
      2. <a> directos             (panel Bootstrap plano)
      3. <li> de las listas internas   (panel con columnas: div > ul > li)
      4. todos los <a> descendientes   (panel con maquetación arbitraria)
    """
    items = contenedor.find_all("li", recursive=False)
    if items:
        return items
    if contenedor.name not in ("div", "nav", "section", "aside"):
        return []

    directos = contenedor.find_all("a", href=True, recursive=False)
    if directos:
        return directos

    listas = contenedor.find_all(["ul", "ol"])
    if listas:
        internos: list[Tag] = []
        for lista in listas:
            if lista.find_parent(["ul", "ol"]) is None:
                internos.extend(lista.find_all("li", recursive=False))
        if internos:
            return internos

    return contenedor.find_all("a", href=True)


def parsear_arbol(contenedor: Tag, base_url: str, prefijo: str = "M",
                  nivel: int = 1, max_nivel: int = 4) -> list[NodoMenu]:
    """Convierte un <ul>/<div> de navegación en una lista de NodoMenu."""
    nodos: list[NodoMenu] = []
    vistos: set[tuple[str, Optional[str]]] = set()

    for item in _items_de(contenedor):
        if item.name == "a":
            titulo, ancla, subs = limpiar_texto(item.get_text()), item, []
        else:
            ancla = _ancla_propia(item)
            subs = _sublistas(item)
            titulo = limpiar_texto(ancla.get_text()) if ancla else ""
            if not titulo:  # <li> con encabezado y lista, sin enlace propio
                cab = item.find(["span", "strong", "h2", "h3", "h4", "summary"])
                titulo = limpiar_texto(cab.get_text()) if cab else ""

        if not es_etiqueta_util(titulo):
            continue

        url = absolutizar(ancla.get("href") if ancla else None, base_url)
        clave = (titulo.lower(), url)
        if clave in vistos:
            continue
        vistos.add(clave)

        nodo = NodoMenu(
            id=f"{prefijo}{len(nodos) + 1:02d}",
            titulo=titulo,
            url=url,
            nivel=nivel,
        )
        if nivel < max_nivel:
            for sub in subs:
                nodo.submenus.extend(
                    parsear_arbol(sub, base_url, prefijo=f"{nodo.id}-S",
                                  nivel=nivel + 1, max_nivel=max_nivel)
                )
        nodos.append(nodo)

    return nodos


# --------------------------------------------------------------------------- #
# API principal
# --------------------------------------------------------------------------- #

def extraer_menus(url: str, render_js: bool = False, selector: Optional[str] = None,
                  max_nivel: int = 4, verificar_robots: bool = True,
                  base_url: Optional[str] = None) -> dict:
    """
    Extrae el árbol de navegación y lo devuelve como diccionario serializable.

    `base_url` resuelve los href relativos. Úsalo al trabajar sobre un
    snapshot en disco, donde la ruta del archivo no sirve como base.
    """
    if verificar_robots and not url.startswith("file://") and not Path(url).exists():
        if not robots_permite(url):
            raise PermissionError(
                f"robots.txt de {urlparse(url).netloc} no permite el acceso "
                f"automatizado a {url}.\n"
                "Solicita autorización a la dependencia o usa --ignore-robots "
                "bajo tu responsabilidad."
            )

    t0 = time.perf_counter()
    html = obtener_html(url, render_js=render_js)
    soup = BeautifulSoup(html, "html.parser")

    nav = localizar_nav(soup, selector)
    if nav is None:
        raise ValueError(
            "No se localizó un contenedor de navegación. "
            "Prueba con --render (menú generado por JS) o pasa --selector."
        )

    # Si el <nav> envuelve un <ul>, parseamos ese <ul>.
    raiz = nav.find(["ul", "ol"]) or nav
    menus = parsear_arbol(raiz, base_url=base_url or url, max_nivel=max_nivel)

    n_sub = sum(len(list(m.descendientes())) for m in menus)
    return {
        "sitio": base_url or url,
        "origen": url,
        "extraido_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "backend": "playwright" if render_js else "requests",
        "selector_usado": selector or f"auto:<{nav.name}>",
        "segundos": round(time.perf_counter() - t0, 2),
        "n_menus": len(menus),
        "n_submenus": n_sub,
        "profundidad_max": max((m.profundidad() for m in menus), default=0),
        "menus": [m.to_dict() for m in menus],
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Extrae menús y submenús a JSON.")
    ap.add_argument("url", help="URL del portal (o ruta a un HTML local).")
    ap.add_argument("-o", "--output", default="data/menus.json")
    ap.add_argument("--render", action="store_true",
                    help="Usa Playwright para menús generados por JavaScript.")
    ap.add_argument("--selector", default=None,
                    help="Selector CSS del contenedor del menú, p.ej. 'nav#principal'.")
    ap.add_argument("--max-nivel", type=int, default=4)
    ap.add_argument("--base-url", default=None,
                    help="Base para resolver href relativos al usar un HTML local.")
    ap.add_argument("--ignore-robots", action="store_true")
    args = ap.parse_args(argv)

    datos = extraer_menus(
        args.url, render_js=args.render, selector=args.selector,
        max_nivel=args.max_nivel, verificar_robots=not args.ignore_robots,
        base_url=args.base_url,
    )

    salida = Path(args.output)
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(datos, ensure_ascii=False, indent=2),
                      encoding="utf-8")

    print(f"[OK] {datos['n_menus']} menús / {datos['n_submenus']} submenús "
          f"(prof. máx. {datos['profundidad_max']}) -> {salida}")
    for m in datos["menus"]:
        print(f"  · {m['titulo']} ({len(m['submenus'])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())


# --------------------------------------------------------------------------- #
# Fallback: portales sin desplegables (la navegación vive en cada sección)
# --------------------------------------------------------------------------- #

def seguir_enlaces(datos: dict, render_js: bool = False, pausa: float = 1.0,
                   max_hijos: int = 15) -> dict:
    """
    Para cada menú SIN hijos, visita su URL y extrae la navegación local de esa
    página como submenús. Descarta los ítems que solo reproducen la barra global
    (comparando contra los rótulos de primer nivel ya conocidos).

    Cuesta una petición por menú vacío: úsalo con moderación y con pausa.
    """
    titulos_top = {m["titulo"].strip().lower() for m in datos["menus"]}
    urls_top = {m.get("url") for m in datos["menus"] if m.get("url")}

    for menu in datos["menus"]:
        if menu["submenus"] or not menu.get("url"):
            continue
        try:
            html = obtener_html(menu["url"], render_js=render_js)
        except Exception as exc:
            print(f"  [!] {menu['titulo']}: {type(exc).__name__}")
            continue

        soup = BeautifulSoup(html, "html.parser")
        candidatos = []
        for sel in SELECTORES_NAV:
            candidatos.extend(soup.select(sel))
        candidatos.extend(ul for ul in soup.find_all("ul") if ul.find("ul"))

        mejores: list[NodoMenu] = []
        for cont in sorted(candidatos, key=_puntuar_contenedor, reverse=True)[:5]:
            nodos = parsear_arbol(cont.find(["ul", "ol"]) or cont,
                                  base_url=menu["url"], prefijo=f"{menu['id']}-S",
                                  nivel=2, max_nivel=3)
            # Nos quedamos con lo que NO es la barra global.
            locales = [n for n in nodos
                       if n.titulo.strip().lower() not in titulos_top
                       and n.url not in urls_top]
            if len(locales) > len(mejores):
                mejores = locales
            if len(mejores) >= max_hijos:
                break

        for i, n in enumerate(mejores[:max_hijos], 1):
            n.id = f"{menu['id']}-S{i:02d}"
            n.nivel = 2
            menu["submenus"].append(n.to_dict())
        print(f"  · {menu['titulo']}: +{len(menu['submenus'])} desde su página")
        time.sleep(pausa)

    datos["n_submenus"] = sum(
        len(list(_iter_desc(m))) for m in datos["menus"])
    datos["profundidad_max"] = max(
        (_prof(m) for m in datos["menus"]), default=0)
    datos["seguimiento_de_enlaces"] = True
    return datos


def _iter_desc(nodo: dict):
    for h in nodo.get("submenus", []):
        yield h
        yield from _iter_desc(h)


def _prof(nodo: dict) -> int:
    if not nodo.get("submenus"):
        return 1
    return 1 + max(_prof(h) for h in nodo["submenus"])
