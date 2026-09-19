"""Dispara el analisis de picks YA (ignora la puerta de las 21:00 Nicaragua)."""
import dashboard

stats = dashboard.generar_picks_dia(forzar=True)
print(stats, flush=True)
