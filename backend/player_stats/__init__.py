"""
Módulo de estadísticas de jugadores.
Fuentes:
  - MLB: statsapi.mlb.com (oficial, gratis, sin API key)
  - Fútbol: API-Sports (requiere FOOTBALL_API_KEY en el .env)
"""

from .player_stats_service import player_stats_service

__all__ = ["player_stats_service"]
