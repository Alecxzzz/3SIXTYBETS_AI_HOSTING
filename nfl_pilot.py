# -*- coding: utf-8 -*-
"""nfl_pilot.py - modulo piloto NFL read-only con caching y tokens efímeros single-use.
Diseñado para insertarse en el proyecto 3SIXTYBETS_AI_HOSTING sin afectar rutas existentes.
Bandera: NFL_STATS_PILOT_ENABLED=0 (apagado por defecto).
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

# ---- Configuración desde environment variables ----
ENABLED = os.environ.get("NFL_STATS_PILOT_ENABLED", "0") == "1"
SEASONS_STR = os.environ.get("NFL_PILOT_SEASONS", "2025,2026")
SEASONS = [s.strip() for s in SEASONS_STR.split(",") if s.strip()]
CACHE_DIR = os.environ.get(
    "NFL_PILOT_CACHE_DIR", os.path.join(os.path.dirname(__file__), "data", "cache", "nflverse")
)
TOKEN_TTL_SECONDS = int(os.environ.get("NFL_PILOT_TOKEN_TTL_SECONDS", "30"))
TOKEN_RATE_LIMIT = int(os.environ.get("NFL_PILOT_TOKEN_RATE_LIMIT", "120"))
DB_PATH = os.environ.get(
    "NFL_PILOT_DB_PATH", os.path.join(os.path.dirname(__file__), "data", "nfl_pilot.db")
)

# ---- Lazy import de nflreadpy ----
_nflreadpy_imported: bool = False


def _ensure_nflreadpy_imported() -> None:
    global _nflreadpy_imported
    if _nflreadpy_imported:
        return
    try:
        import nflreadpy as nfl  # type: ignore
        try:
            nfl.config(cache_dir=CACHE_DIR)
        except Exception:
            pass
        _nflreadpy_imported = True
    except ImportError:
        _nflreadpy_imported = True
