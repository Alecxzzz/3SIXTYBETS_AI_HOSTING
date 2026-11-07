# Radar HD — replay con tracking real (SkillCorner Open Data, MIT)

Página **Radar HD** del menú: reproduce las **posiciones reales de los 22 jugadores
y del balón**, capturadas con visión por computadora sobre la retransmisión.
Es lo más parecido al Live Match Tracker de Sportradar que se puede tener
gratis y con licencia limpia.

## Qué es y qué no es

- **Sí**: tracking real (x/y de cada jugador + balón), nombres, dorsales, equipos
  y colores de camiseta oficiales del partido.
- **No**: no es en vivo. Son **partidos ya jugados** de la A-League 2024/25 que
  SkillCorner publicó como open data. Ninguna fuente gratuita da estas posiciones
  en directo: eso es Hawk-Eye / Second Spectrum / Sportradar LMT (de pago).

## Licencia

[SkillCorner Open Data](https://github.com/SkillCorner/opendata) — **MIT** (código y
datos). El crédito "Datos: SkillCorner Open Data (MIT)" se muestra en la propia
página del radar.

## Cómo funciona

```
SkillCorner (Git LFS, ~90 MB/partido)
        │  ingestar_tracking_skillcorner.py
        ▼
demo_tracking/<id>.json.gz   (~0,7 MB por partido, 2,5 fps, decímetros)
        │  GET /stats/tracking-demo            -> lista (index.json)
        │  GET /stats/tracking-demo/<id>       -> JSON ya gzipeado
        ▼
Frontend: RadarHD.jsx (canvas) — play/pausa, scrub y 10x/30x/60x
```

El JSON de cada partido trae `balon` y `camino` como arrays planos
`[x0, y0, x1, y1, ...]` con enteros en decímetros (`-1` = sin detección), y `fps`
para saber cuántos frames hay por segundo de juego. El frontend interpola entre
frames, así que con 2,5 fps el movimiento se ve continuo.

## Añadir más partidos

```bash
# uno concreto
python ingestar_tracking_skillcorner.py 1886347

# los 10 publicados (descarga ~900 MB en total, tarda)
python ingestar_tracking_skillcorner.py --todos
```

El script actualiza solo `demo_tracking/index.json`, así que la página muestra el
nuevo partido sin tocar código. Después hay que commitear `demo_tracking/` y
`git push` para que Railway lo meta en la imagen (el Dockerfile hace `COPY . .`).

IDs publicados: `1874553`, `1886347`, `1899585`, `1925299`, `1927964`,
`1953632`, `2010085`, `2011166`, `2013725`, `2015213`.

## Costes

- Disco/repo: ~0,7 MB por partido (comprimido).
- RAM en runtime: baja; el backend solo lee el `.gz` y lo sirve con
  `Content-Encoding: gzip` y `Cache-Control: max-age=86400`.
- No hay llamadas a APIs de terceros en runtime.
