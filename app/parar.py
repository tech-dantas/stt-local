"""Desliga o STT Portátil que estiver rodando (usado pelo PARAR.bat)."""

import json
import sys
import urllib.request
from pathlib import Path

config = Path(__file__).resolve().parent.parent / "dados" / "config.json"
try:
    porta = int(json.loads(config.read_text(encoding="utf-8-sig")).get("porta", 8036))
except (OSError, ValueError):
    porta = 8036
try:
    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{porta}/api/parar", data=b"", method="POST"), timeout=5)
    print(f"  STT Portátil (porta {porta}) desligado.")
except OSError:
    print(f"  Nada rodando na porta {porta}.")
    sys.exit(0)
