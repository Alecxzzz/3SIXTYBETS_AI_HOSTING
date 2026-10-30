# -*- coding: utf-8 -*-
"""
Props de jugador: apartado informativo (no es motor de picks).

Este paquete NO apuesta ni decide nada: para cada prop que el bookmaker
publica sobre un jugador, muestra su historico real en los ultimos N
partidos (la "racha") junto a la cuota, para que el usuario juzgue.

Modulos:
    mercados.py     -> parseo de las props del bookmaker (3 formatos)
    proveedores.py  -> historico por partido de cada jugador (ESPN / statsapi)
    service.py      -> une ambas cosas y calcula racha, % y edge

Se alimenta desde props_scheduler.py (hilo daemon, cada 90 minutos).
"""

__all__ = ["mercados", "proveedores", "service"]

# Nota: no se reexportan aqui las funciones de service a proposito. service
# importa mercados y proveedores, y reexportarlo desde el paquete crea un
# ciclo de import que rompe cualquier import parcial (p.ej. solo mercados en
# los tests). Se usa `from backend.props import service`.
