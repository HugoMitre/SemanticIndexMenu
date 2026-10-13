#!/usr/bin/env bash
# Comprueba que cada archivo esté en su carpeta antes de arrancar.
cd "$(dirname "$0")"
faltan=0
revisar() {
  if [ -f "$1" ]; then printf '  ok   %s\n' "$1"
  else printf '  FALTA %s\n' "$1"; faltan=$((faltan+1)); fi
}
echo "Raíz del proyecto"
for f in app.py iniciar.sh requirements.txt README.md; do revisar "$f"; done
echo "core/"
for f in __init__.py scraper.py dinamico.py similitud.py complejidad.py \
         amplitud_ref.py estadisticos.py catalogo.py; do revisar "core/$f"; done
echo "web/"
for f in index.html estilos.css app.js; do revisar "web/$f"; done
echo "tests/"
for f in fixture_portal.html fixture_megamenu.html; do revisar "tests/$f"; done

echo
if [ "$faltan" -eq 0 ]; then
  echo "Completo. Arranca con:  ./iniciar.sh"
else
  echo "Faltan $faltan archivos."
fi
