# Widget Live Match Tracker de Sportradar (Engagement Tools)

El "Radar del partido" de Estadísticas usa, en este orden:

1. **Widget oficial `match.lmtPlus`** (Live Match Tracker) — solo si hay licencia.
2. **Radar propio** (`/stats/radar`, datos de FotMob) — respaldo automático.

## Por qué no basta `SPORTRADAR_API_KEY`

El LMT es un producto de **Engagement Tools**, no la API de datos:

- Se sirve como widget desde `https://widgets.sir.sportradar.com/{clientId}/widgetloader`.
- Necesita un **clientId** propio que Sportradar entrega al contratar Engagement Tools
  (contrato/entitlement aparte de la API key).
- Necesita el **matchId** de Sportradar del partido (`sr:match:XXXXXXXX`), distinto de
  los ids de ESPN/FotMob que usamos internamente.

La API key actual responde **403** en `soccer/trial/v4`, así que tampoco sirve para
resolver el matchId por API: por eso el mapeo se hace a mano/por integración con
`SPORTRADAR_WIDGET_MATCHES`.

## Qué pedirle a Sportradar

Al ejecutivo de cuenta (sales/support):

1. Acceso a **Engagement Tools → Live Match Tracker (LMT Plus)** para `threesixtybets.xyz`.
2. El **clientId** del widget (y confirmación de qué dominios están permitidos en el
   whitelist del widget; normalmente hay que declarar el dominio).
3. Autorización para usar el **feed de datos + widget** en un sitio de estadísticas/
   pronósticos (revisar si la licencia cubre uso comercial o requiere atribución).
4. Opcional: variantes **LCO** (video) y **LCP** (highlights) si se quieren dentro del mismo widget.

Coste típico: licencia mensual por deporte/competición + fee de integración. No hay
plan gratuito ni sandbox público para el LMT.

## Configuración (variables de entorno)

```env
SPORTRADAR_WIDGET_CLIENT_ID=client1                      # clientId que da Sportradar
SPORTRADAR_WIDGET_LAYOUT=single                          # single | double | topdown
SPORTRADAR_WIDGET_MATCHES={"401123456":"43219876"}       # event_id -> matchId
```

- `SPORTRADAR_WIDGET_MATCHES` es JSON: clave = `event_id` interno (el mismo que usa
  `/stats/game` y `/stats/radar`), valor = número de `sr:match:` de Sportradar.
- Se puede ir agregando partido por partido; los que no estén en el mapa siguen
  mostrando el radar propio.

## Endpoints

`GET /stats/widget?sport=soccer&event_id=<id>` (requiere sesión):

```json
// con licencia y matchId
{
  "ok": true,
  "disponible": true,
  "widget": "match.lmtPlus",
  "client_id": "client1",
  "match_id": "43219876",
  "layout": "single",
  "loader": "https://widgets.sir.sportradar.com/client1/widgetloader"
}

// sin clientId
{ "ok": true, "disponible": false, "motivo": "sin_licencia", "detalle": "..." }

// con clientId pero sin mapeo para ese partido
{ "ok": true, "disponible": false, "motivo": "sin_match_id", "detalle": "..." }
```

## Integración en el frontend

`src/components/RadarLmt.jsx`:

1. Pregunta `/stats/widget`.
2. Si `disponible`, instala el stub global `SIR`, carga el `widgetloader` y llama:

```js
SIR("addWidget", "#sr-lmt-<matchId>", "match.lmtPlus", {
  matchId: 43219876,
  layout: "single",
});
```

3. Si el widget no pinta nada en 9 s (clientId sin entitlement, script bloqueado,
   dominio no whitelisteado), cae a `<RadarPartido/>` para no dejar el hueco vacío.

`Stats.jsx` monta `<RadarLmt sport={sport} eventId={eventId} />`, así que el cambio a
widget oficial es solo configuración: no hay que tocar código.

## Alternativa declarativa (sin SIR)

Si se prefiere HTML plano (por ejemplo en otra página):

```html
<div
  id="sr-widget"
  data-sr-widget="match.lmtPlus"
  data-sr-match-id="43219876"
  data-sr-layout="single"
  data-sr-tabs-position="top"
></div>
<script type="application/javascript"
        src="https://widgets.sir.sportradar.com/client1/widgetloader" async></script>
```

Recordar reemplazar `client1` por el clientId real en la URL del loader.

## Referencias

- Doc oficial LMT: https://apidocs.sportradar.com/resources/widgets/docs/lmt/lmt
- Índice de widgets: https://developer.sportradar.com/widgets/llms.txt
- Estado actual sin licencia: radar propio con tiros/xG/momentum/zonas (FotMob).
