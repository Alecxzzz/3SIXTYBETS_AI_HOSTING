# Plano: Carta del dashboard (3SIXTYBETS)

## 1. Dashboard: cada pick es una mini-carta
- Encabezado: deporte + badge GOLDEN PICK (si aplica)
- Titular: `eventName` (grande)
- Mercado: negrita + selección
- Cuota grande: real si existe, estimada si no
- Bookmakers reales (solo si `odds_real_len > 0`): "Bet365, Winpot MX"
- Pie de carta: "3SIXTYBETS · Inteligencia deportiva con valor"
- GOLDEN PICK: borde exterior + interior dorados, badge dorado, botón "Compartir carta" dorado
- Aciertos: badge ✅ ACIERTO + estadística de efectividad

## 2. Botón Compartir carta
- Exporta PNG (canvas 1080x1350)
- Copia al portapapeles texto tipo:
  ```
  3SIXTYBETS · Inteligencia deportiva con valor
  [evento]
  Mercado: xxx
  Selección: xxx @ cuota
  Fuentes: Bet365 · Winpot MX (cuotas reales)
  Confianza: xx%
  ```
- Mensaje final: "Foto de la carta lista para pegar en WhatsApp/Telegram."

## 3. Reglas de cuotas
- Siempre se muestra cuota (real o estimada)
- En aciertados se muestran cuotas del momento del partido
- Si `odds_real_len == 0`, la carta dice "Estimada"
- No se publican picks sin cuota/publicables (filtro backend)

## 4. Backend: campo extra
- `odds_real_len`: cantidad de bookmakers reales que aportaron cuota
- Se incluye en picks del dashboard para que el frontend decida qué mostrar
