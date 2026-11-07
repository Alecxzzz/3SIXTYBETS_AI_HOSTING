# Solicitud de licencia: Live Match Tracker (Engagement Tools)

Estado a día de hoy (comprobado en producción):

- La integración del widget **está hecha** (`RadarLmt.jsx` + `WidgetLmtPrueba.jsx`).
- El iframe de su demo **no se puede embeber**: su CSP es
  `frame-ancestors 'self' https://*.sportradar.com https://*.betradar.com ...`.
- El clientId público de la demo (`betradar`) **carga el loader pero no pinta el widget**
  en `threesixtybets.xyz` → el dominio no está en la whitelist de ese clientId.
- Nuestra `SPORTRADAR_API_KEY` devuelve **403** en `soccer/trial/v4`, así que tampoco
  podemos resolver los `matchId` por API.

Conclusión: solo falta la parte comercial. Lo que hay que pedir es exactamente esto:

## Checklist para Sportradar

1. **clientId** de Engagement Tools para `threesixtybets.xyz`.
2. **Whitelist de dominios**: `threesixtybets.xyz`, `www.threesixtybets.xyz` y los
   dominios de preview de Vercel (`*.vercel.app`) para pruebas.
3. **Producto**: `match.lmtPlus` (LMT Plus) para **fútbol**, con layout `single` y
   `double`; opcionalmente LCO (vídeo) y LCP (highlights).
4. **Identificadores de partido**: cómo obtener el `matchId` de cada partido
   (¿Soccer API v4 producción? ¿el propio widget acepta búsqueda?). Necesitamos
   mapear nuestros eventos a `matchId`.
5. **Precio**: cuota mensual por deporte/competición, fee de integración y si el
   plan incluye las 13 ligas que ya mostramos (Premier, LaLiga, Serie A, Bundesliga,
   Ligue 1, Liga MX, MLS, Liga BetPlay, Brasileirao, Argentina, etc.).
6. **Uso permitido**: sitio de estadísticas y pronósticos con registro de usuarios;
   confirmar atribución/branding requeridos.

## Correo listo para enviar

> Asunto: Engagement Tools (LMT Plus) — integration ready, need clientId for threesixtybets.xyz

Hello,

We run **threesixtybets.xyz**, a football statistics and predictions platform
(React frontend on Vercel, FastAPI backend on Railway, users behind login).

We have already implemented your **Engagement Tools Live Match Tracker
(`match.lmtPlus`)** following the official programmatic integration
(`SIR('addWidget', '#sr-widget', 'match.lmtPlus', { matchId, layout })`).

We validated the flow against your public demo client (`betradar`): the
`widgetloader` script loads correctly on our domain, but the widget does not
render, which we understand is the expected behaviour for a clientId whose
domain whitelist does not include us.

To go live we would need:

1. A **clientId** for Engagement Tools for our domain.
2. **Domain whitelist**: `threesixtybets.xyz`, `www.threesixtybets.xyz` and a
   `*.vercel.app` preview domain for staging.
3. **LMT Plus for soccer** (single and double layouts). LCO/LCP optional.
4. Guidance on how to obtain the **`matchId`** for each fixture. Our current
   API key returns 403 on `soccer/trial/v4`, so we need either production
   Soccer API access or the recommended way to resolve match ids.
5. **Pricing**: monthly fee, integration fee, and which competitions are covered
   (we currently surface Premier League, LaLiga, Serie A, Bundesliga, Ligue 1,
   Liga MX, MLS, Liga BetPlay, Brasileirão, Argentine Primera and more).

Could you send a quote and next steps? Happy to jump on a short call.

Best regards,
— 3SIXTYBETS

## Alternativas si Sportradar no responde

- **LSports** y **Flashlive (Sportradar partner / LSports widgets)**: ofrecen
  "live match tracker" animado con licencia por deporte, normalmente más barato.
- **Pipeline propio con visión por computadora** (`SoccerNet/sn-gamestate`,
  `roboflow/sports`): sin licencia de datos, pero con GPU alquilada
  (~0,15–0,40 USD/hora) y riesgo legal si se deriva de emisiones de terceros.

## Mientras tanto

- **Estadísticas → Radar del partido**: mapa de tiros con xG, momentum y zonas
  (datos reales de FotMob).
- **Radar HD**: replay con tracking real de 22 jugadores + balón (SkillCorner
  Open Data, MIT) para enseñar cómo se verá el radar licenciado.
