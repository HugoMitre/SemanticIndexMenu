"""
menu_dinamico.py
================
Extracción de menús por INTERACCIÓN real en el navegador.

Por qué existe
--------------
El parser estático asume que el submenú es un <ul> anidado dentro del <li>.
Los portales modernos (incluidos varios estatales mexicanos) usan mega-menús:
la barra superior solo contiene los rótulos, y los paneles de submenú viven
FUERA del <nav> —al final del <body>, o inyectados por React— y se revelan al
pasar el cursor. Sobre ese DOM, el parser estático encuentra N menús y 0 submenús.

Estrategia: DIFF DE ENLACES VISIBLES
------------------------------------
En vez de suponer una estructura de DOM, se mide un efecto observable:

    1. Se localiza la barra y se marca cada disparador de primer nivel.
    2. Se neutraliza la navegación (preventDefault en captura) para poder
       hacer clic sin abandonar la página.
    3. Para cada disparador: se toma la foto de los enlaces VISIBLES, se hace
       hover (y clic si el hover no produjo nada), se vuelve a fotografiar.
    4. Los enlaces nuevos son, por definición, su submenú.

Esto es agnóstico al DOM: funciona con <ul> anidados, paneles desacoplados,
portales de React y menús construidos con CSS puro.

Uso
---
    python menu_dinamico.py https://www.aguascalientes.gob.mx/ -o data/menus.json
    python menu_dinamico.py https://... --guardar-html data/rendered.html
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .scraper import (NodoMenu, absolutizar, es_etiqueta_util,
                       limpiar_texto, USER_AGENT, robots_permite)

# --------------------------------------------------------------------------- #
# JavaScript inyectado en la página
# --------------------------------------------------------------------------- #

JS_MARCAR_BARRA = r"""
(selectorForzado) => {
  const SELS = ["nav", "[role='navigation']", "header", ".navbar",
                ".main-navigation", ".menu-principal", ".main-menu",
                "#menu", "#nav", "#menu-principal", "ul.menu",
                "ul.navbar-nav", "ul.nav"];
  let cands = [];
  if (selectorForzado) {
    const n = document.querySelector(selectorForzado);
    if (n) cands.push(n);
  } else {
    SELS.forEach(s => document.querySelectorAll(s).forEach(n => cands.push(n)));
    document.querySelectorAll('ul').forEach(u => {
      if (u.querySelector('ul')) cands.push(u);
    });
  }
  const puntuar = (n) => {
    const as = n.querySelectorAll('a[href]');
    if (!as.length) return -1;
    const anidados = Array.from(n.querySelectorAll('li'))
                          .filter(li => li.querySelector('ul,ol')).length;
    const popups = n.querySelectorAll(
        '[aria-haspopup],[aria-controls],[data-target],[data-bs-target]').length;
    const largos = Array.from(as)
        .filter(a => (a.innerText || '').trim().length > 60).length;
    return 3 * anidados + 2 * popups + as.length - 1.5 * largos;
  };
  let best = null, bs = -1;
  cands.forEach(n => { const s = puntuar(n); if (s > bs) { bs = s; best = n; } });
  if (!best) return null;

  // Lista de primer nivel: el propio contenedor si es <ul>, si no el primero dentro.
  const lista = (best.tagName === 'UL' || best.tagName === 'OL')
                ? best : best.querySelector('ul, ol');
  const items = lista ? Array.from(lista.children).filter(c => c.tagName === 'LI')
                      : [];

  const salida = [];
  items.forEach((li, i) => {
    const trig = li.querySelector('a[href], button, summary, [role="button"]') || li;
    const txt = (trig.innerText || trig.textContent || '').trim();
    if (!txt) return;
    trig.setAttribute('data-mcx-top', String(salida.length));
    salida.push({
      i: salida.length,
      texto: txt,
      href: trig.getAttribute ? (trig.getAttribute('href') || null) : null
    });
  });
  // Neutraliza la navegación para poder hacer clic sin salir de la página.
  document.querySelectorAll('[data-mcx-top]').forEach(el => {
    el.addEventListener('click', e => e.preventDefault(), true);
  });
  return { contenedor: best.tagName.toLowerCase() +
                       (best.id ? '#' + best.id : '') +
                       (best.className ? '.' + String(best.className).trim()
                                              .split(/\s+/).join('.') : ''),
           puntaje: bs, items: salida };
}
"""

JS_ENLACES_VISIBLES = r"""
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' &&
           parseFloat(s.opacity || '1') > 0.05;
  };
  const out = [];
  document.querySelectorAll('a[href]').forEach(a => {
    if (!visible(a)) return;
    const t = (a.innerText || a.textContent || '').trim().replace(/\s+/g, ' ');
    if (!t) return;
    out.push([t, a.getAttribute('href'), a.hasAttribute('data-mcx-top')]);
  });
  return out;
}
"""


# --------------------------------------------------------------------------- #
# Extracción
# --------------------------------------------------------------------------- #

def extraer_menus_interactivo(
    url: str,
    selector: Optional[str] = None,
    espera_ms: int = 700,
    timeout: int = 45,
    headless: bool = True,
    guardar_html: Optional[str] = None,
    verificar_robots: bool = True,
) -> dict:
    """Abre el portal, interactúa con cada rótulo y devuelve el árbol completo."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Falta Playwright:  pip install playwright && playwright install chromium"
        ) from exc

    es_local = url.startswith("file://") or Path(url).exists()
    if verificar_robots and not es_local and not robots_permite(url):
        raise PermissionError(
            f"robots.txt no permite el acceso automatizado a {url}. "
            "Usa --ignore-robots bajo tu responsabilidad."
        )
    destino = url if not es_local or url.startswith("file://") \
        else Path(url).resolve().as_uri()

    t0 = time.perf_counter()
    traza: list[str] = []

    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=headless)
        pagina = navegador.new_page(user_agent=USER_AGENT, locale="es-MX",
                                    viewport={"width": 1440, "height": 900})
        pagina.goto(destino, timeout=timeout * 1000, wait_until="domcontentloaded")
        try:
            pagina.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass                      # sitios con polling nunca quedan "idle"
        pagina.wait_for_timeout(espera_ms)

        if guardar_html:
            Path(guardar_html).parent.mkdir(parents=True, exist_ok=True)
            Path(guardar_html).write_text(pagina.content(), encoding="utf-8")

        barra = pagina.evaluate(JS_MARCAR_BARRA, selector)
        if not barra or not barra["items"]:
            navegador.close()
            raise ValueError(
                "No se localizó la barra de navegación. Ejecuta primero:\n"
                f"    python3 diagnostico.py {url}\n"
                "y pasa el selector correcto con --selector."
            )

        menus: list[NodoMenu] = []
        for it in barra["items"]:
            if not es_etiqueta_util(limpiar_texto(it["texto"])):
                continue
            trig = pagina.query_selector(f'[data-mcx-top="{it["i"]}"]')
            if trig is None:
                continue

            # Estado de reposo: alejamos el cursor para cerrar paneles previos.
            pagina.mouse.move(2, 2)
            pagina.wait_for_timeout(250)
            antes = {(t, h) for t, h, _ in pagina.evaluate(JS_ENLACES_VISIBLES)}

            nuevos, via = [], "hover"
            try:
                trig.hover(timeout=3000)
                pagina.wait_for_timeout(espera_ms)
                nuevos = _diff(pagina, antes)
            except Exception:
                pass

            if not nuevos:                       # el hover no abrió nada: probamos clic
                try:
                    trig.click(timeout=3000)
                    pagina.wait_for_timeout(espera_ms)
                    nuevos, via = _diff(pagina, antes), "click"
                except Exception:
                    pass

            nodo = NodoMenu(
                id=f"M{len(menus) + 1:02d}",
                titulo=limpiar_texto(it["texto"]),
                url=absolutizar(it["href"], url),
                nivel=1,
            )
            vistos: set[tuple[str, Optional[str]]] = set()
            for texto, href in nuevos:
                titulo = limpiar_texto(texto)
                if not es_etiqueta_util(titulo):
                    continue
                u = absolutizar(href, url)
                if (titulo.lower(), u) in vistos:
                    continue
                vistos.add((titulo.lower(), u))
                nodo.submenus.append(NodoMenu(
                    id=f"{nodo.id}-S{len(nodo.submenus) + 1:02d}",
                    titulo=titulo, url=u, nivel=2))

            traza.append(f"{nodo.titulo}: {len(nodo.submenus)} vía {via}")
            menus.append(nodo)

        navegador.close()

    n_sub = sum(len(m.submenus) for m in menus)
    return {
        "sitio": url,
        "extraido_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "backend": "playwright-interactivo",
        "selector_usado": selector or f"auto:{barra['contenedor']}",
        "segundos": round(time.perf_counter() - t0, 2),
        "n_menus": len(menus),
        "n_submenus": n_sub,
        "profundidad_max": 2 if n_sub else 1,
        "traza": traza,
        "menus": [m.to_dict() for m in menus],
    }


def _diff(pagina, antes: set) -> list[tuple[str, str]]:
    """Enlaces visibles que aparecieron y que no son de la barra de primer nivel."""
    despues = pagina.evaluate(JS_ENLACES_VISIBLES)
    return [(t, h) for t, h, es_top in despues if not es_top and (t, h) not in antes]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Extrae menús interactuando con la página (mega-menús).")
    ap.add_argument("url")
    ap.add_argument("-o", "--output", default="data/menus.json")
    ap.add_argument("--selector", default=None)
    ap.add_argument("--espera", type=int, default=700, help="ms tras cada hover")
    ap.add_argument("--ver", action="store_true",
                    help="Muestra el navegador (útil para depurar).")
    ap.add_argument("--guardar-html", default=None,
                    help="Guarda el DOM renderizado para trabajar offline.")
    ap.add_argument("--ignore-robots", action="store_true")
    args = ap.parse_args(argv)

    datos = extraer_menus_interactivo(
        args.url, selector=args.selector, espera_ms=args.espera,
        headless=not args.ver, guardar_html=args.guardar_html,
        verificar_robots=not args.ignore_robots)

    salida = Path(args.output)
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(datos, ensure_ascii=False, indent=2),
                      encoding="utf-8")

    print(f"[OK] {datos['n_menus']} menús / {datos['n_submenus']} submenús "
          f"en {datos['segundos']}s -> {salida}")
    print(f"     contenedor: {datos['selector_usado']}")
    for linea in datos["traza"]:
        print(f"  · {linea}")
    if datos["n_submenus"] == 0:
        print("\n[!] Ningún submenú. Ejecuta:  python3 diagnostico.py " + args.url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
