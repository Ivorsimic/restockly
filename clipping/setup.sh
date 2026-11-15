#!/usr/bin/env bash
# Instalacija za Mac / Linux. Pokreni jednom: bash setup.sh
set -e
cd "$(dirname "$0")"
if ! command -v ffmpeg >/dev/null; then
  if command -v brew >/dev/null; then brew install ffmpeg
  elif command -v apt-get >/dev/null; then sudo apt-get install -y ffmpeg
  else echo "Instaliraj ffmpeg ručno: https://ffmpeg.org/download.html"; exit 1; fi
fi
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
echo
echo "Gotovo. Svaki put prije rada:  source .venv/bin/activate"
echo "Probaj:  python clipper.py \"https://www.youtube.com/watch?v=...\""
