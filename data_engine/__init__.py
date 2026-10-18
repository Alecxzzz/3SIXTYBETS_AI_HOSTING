"""
data_engine — Motor de busqueda de datos reales + verificacion de picks.

Expone un APIRouter que se monta en el backend principal (main.py):
  POST /data/partido        -> datos completos de un partido
  POST /data/contexto-ia    -> contexto en texto para la IA
  POST /search/match        -> resumen del partido
  POST /search/prop         -> verifica ACIERTO/FALLO de un pick
"""
from data_engine.router import router

__all__ = ["router"]
