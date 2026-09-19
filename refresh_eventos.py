"""Refresca los eventos de la TV CON validacion de stream vivo."""
import event_scheduler

print(event_scheduler.refresh_once(), flush=True)
