#!/usr/bin/env bash
# Arranca la aplicación del ICN. Crea el entorno virtual la primera vez.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creando el entorno virtual…"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  echo "Instalando dependencias (tarda unos minutos la primera vez)…"
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

exec ./.venv/bin/python app.py
