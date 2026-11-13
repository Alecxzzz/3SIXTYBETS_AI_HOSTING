# Motor ELO + MONTE CARLO

Sustituye, **en NBA**, el criterio anterior de elección de picks
(`_pick_deterministico`: ordenar por peso del mercado y cuota más baja) por una
**probabilidad calculada** del partido. La IA deja de decidir: solo puede
redactar.

Estado: **NBA activo**. El resto de deportes sigue con su criterio anterior
hasta que cada uno tenga su calibración (ver *Añadir un deporte*).

---

## Cómo funciona (5 piezas)

```
resultados ESPN (3 temporadas)
      ↓
  [1] ELO            fuerza de cada equipo, ajustada por el rival
      ↓
  [2] ELO → puntos   margen = a + b·elo_diff + c·descanso
                     total  = t0 + t1·ritmo reciente
      ↓
  [3] MONTE CARLO    20.000 simulaciones de los DOS marcadores
      ↓
  [4] DECISIÓN       prob vs 1/cuota → edge → apostar o no
      ↓
  [5] MEDICIÓN       ai_picks_prob → Brier / log-loss / ROI
```

## Archivos

| Archivo | Qué hace |
|---|---|
| `backend/apuestas/elo.py` | Ratings, ingesta de ESPN, ajuste por mínimos cuadrados, persistencia |
| `backend/apuestas/simulacion.py` | Monte Carlo: dos marcadores correlacionados + prórroga |
| `backend/apuestas/motor.py` | Precio de cada mercado del bookmaker, edge y decisión |
| `backend/apuestas/calibrar.py` | Ajusta y **mide** el modelo contra 2641 partidos históricos |
| `backend/apuestas/backtest.py` | Brier / log-loss / ROI de los picks ya resueltos |
| `motor_preview.py` | Vista previa de hoy **sin escribir picks** |
| `generar_picks_nba.py` | Genera picks solo de NBA con el motor encendido |
| `verificar_motor_nba.py` | Comprueba en la BD qué eligió el motor y qué midió |
| `test_elo.py` / `test_simulacion.py` / `test_motor.py` | 48 tests |

## Tablas nuevas (las crea `crear_tablas_elo.py` o `db.init_db()`)

- `elo_ratings` — rating, partidos jugados, forma reciente (pf/pa/total).
- `elo_calibracion` — coeficientes y sigmas medidos, por deporte.
- `ai_picks_prob` — **la probabilidad que dijo el modelo para cada pick**.
  Sin esta tabla no se puede calcular Brier: `ai_picks` guarda la cuota y el
  resultado, pero no lo que el modelo creía.

## Uso

```bash
python crear_tablas_elo.py                    # una vez (idempotente)
python -m backend.apuestas.calibrar nba       # calibra y MIDE (2-3 min)
python motor_preview.py                       # qué apostaría hoy, sin publicar
python generar_picks_nba.py --forzar          # publica los picks de NBA
python -m backend.apuestas.backtest           # calidad de lo ya resuelto
```

## El analista (panel de picks) también usa este motor

Desde este cambio, `ai/analista_picks.py` **precia los picks NBA con Elo + Monte
Carlo**, no con el modelo de forma de 10 partidos que tenía antes:

| Antes | Ahora |
|---|---|
| `forma_equipo()` → medias de 10 partidos | `motor.predecir()` → ratings Elo + descanso |
| Margen y total a mano | `simulacion.simular_nba()` → 10.000 simulaciones |
| No ajustaba por rival | Ajusta por rival (es lo que hace Elo) |
| Sin calibrar | Calibrado con 2.632 partidos (Brier 0.210) |

Orden de decisión en `_analizar_nba()`:

1. `_salida_elo()` — motor Elo + MC. Devuelve también el **intervalo** de la
   probabilidad (`prob_low`/`prob_high`), que el modelo de forma no podía dar.
2. Si no hay ratings (o el mercado no se modela) → respaldo: modelo de forma.

El respaldo también cubre los mercados de **props de jugador** (`Minimo de
puntos del jugador`), que el motor de partido no simula: ahí manda el modelo de
forma con los promedios de temporada del plantel.

Notas de rendimiento (un lote de 25 picks tarda ~26 s):

- `equipo_id()` cachea en memoria: antes se preguntaba el id a ESPN **dos veces
  por pick** (50 peticiones por lote).
- `elo.estado()` cachea los ratings 12 h; a partir del primer pick son gratis.
- 10.000 simulaciones por pick (no 20.000): el precio no cambia y baja a la mitad
  el tiempo.

## Cuántos picks por análisis

`POST /analisis/pendientes` analiza **25 picks por defecto** (antes 10), y acepta
hasta **60** con `{"limite": N}`. El resumen devuelve, además de
`valor / neutro / evitar`, dos contadores nuevos:

- `dudosos` — el modelo discrepa más de 10 puntos de la cuota. **No se publican.**
- `revisar` — el edge supera el 25%, imposible en un mercado líquido.
- `motor_elo_mc` — cuántos picks usaron el motor (el resto, el respaldo).


## Resultado de la calibración NBA (3 temporadas, 2.632 partidos)

```
margen = -3.15 + 4.689·elo_diff/100 + 0.368·descanso_diff
total  = -18.54 + 1.057·ritmo
sigma margen 14.50 | sigma total 18.89 | rho equipos 0.26 | prórroga 6.5%

Brier 0.21015   (50/50: 0.25000 | siempre local: 0.24898)  → mejora +15.9%
MAE del margen 11.35 pts   (baseline 13.05)                → mejora +13.0%
```

## Variables de entorno

| Variable | Default | Qué controla |
|---|---|---|
| `DASHBOARD_MOTOR_MC` | `nba` | Deportes con motor probabilístico (`""` lo apaga) |
| `MOTOR_MC_MIN_EDGE` | `0.03` | Edge mínimo para publicar |
| `MOTOR_MC_MAX_EDGE` | `0.15` | Tope superior ("demasiado bueno para ser verdad") |
| `MOTOR_MC_MAX_DESVIO` | `0.10` | Desacuerdo máximo con la cuota, en probabilidad |
| `MOTOR_MC_MAX_DESVIO_PUNTOS` | `6.0` | Desacuerdo máximo con la línea, en puntos |
| `MOTOR_MC_MAX_PERDIDA` | `0.05` | Pérdida máxima admitida en el peor caso (p5) |
| `MOTOR_MC_SIMS` | `20000` | Simulaciones por partido |
| `MOTOR_MC_VERSION` | `elo-mc-nba-v1` | Etiqueta de versión (trazabilidad) |

Apagar el motor: `DASHBOARD_MOTOR_MC=""` → el dashboard se comporta **exactamente**
como antes, sin revertir código.

---

## Las 4 redes de seguridad (por qué no publica "edges" del 30%)

Un modelo propio SIEMPRE encuentra edges en un mercado líquido. Casi siempre es
el modelo el que está mal, no el mercado. Por eso hay cuatro filtros:

1. **Edge mínimo** (`3%`): por debajo no compensa el riesgo.
2. **Tope de edge** (`15%`): con cuotas largas, 8 puntos de diferencia de
   probabilidad se convierten en 21% de edge. Eso no es valor.
3. **Desvío con la cuota** (`10 pts`) y **desvío en puntos** (`6 pts`): si el
   modelo pone a Detroit +18.5 y la línea es +2.5, no sabe algo que el mercado
   sí sabe (lesión, descanso).
4. **Peor caso** (pérdida ≤ `5%` en el percentil 5): apuesta solo si incluso
   equivocándose un poco sigue viva.

Sin las redes, la primera corrida publicó `Más de 218.5 @ 1.909` con edge
**+33.8%**. El problema real: el total esperado era 229.8 y la línea 218.5,
porque el modelo arrastraba la media de 3 temporadas. Se corrigió anclando el
nivel a la ventana de 21 días **y** se añadieron las redes.

## Dos bugs reales que encontraron los tests

- **Sistema singular**: si la columna de descanso es constante (partidos sin
  fecha), `X'X` no se puede invertir y el ajuste devolvía ceros → el modelo
  predecía **margen 0 para todos los partidos**, en silencio. Ahora reajusta sin
  esa columna y lo reporta (`sin_descanso: true`).
- **Datos corruptos**: 9 partidos con marcador imposible inflaban `sigma_total`
  de 18.9 a 23.3, lo que hacía que cualquier diferencia con la línea pareciera
  un edge. Ahora se filtran por rango de cordura antes de calibrar.

## Añadir un deporte

1. Añadir su configuración en `elo.CONFIG` (`k`, `hfa`, `mov`, `rango_total`).
2. Añadir su ruta de ESPN en `elo.RUTAS` (ya están `nfl`, `mlb`, `wnba`).
3. Crear el simulador que corresponda en `simulacion.py`
   (Poisson de carreras para MLB, normal correlacionada para NFL).
4. Mapear sus mercados en `motor.clasificar` (MLB y NFL comparten
   `ganador`/`handicap`/`total`).
5. `python -m backend.apuestas.calibrar <deporte>` y **pasar el criterio**:
   Brier mejor que "siempre gana el local" y MAE mejor que predecir 0.
6. Añadirlo a `DASHBOARD_MOTOR_MC`. Si no pasa el criterio, se queda apagado.

**Tenis** es el caso aparte: ATP y Sofascore están bloqueados por Cloudflare
(los `test_atp_scrape.py` / `test_sofascore_tennis.py` del repo lo documentan),
así que no hay histórico limpio de partidos por jugador. Solo se puede sembrar
el Elo desde el ranking y acumular hacia adelante: arrancaría **sin calibrar**.

## Límites conocidos (no prometer lo que no hay)

- **Elo no conoce lesiones, alineaciones ni traspasos.** En noviembre arrastra
  todavía la temporada anterior (se encoge un 25% al cambiar de año).
- **No hay histórico de cuotas de cierre**, así que **CLV no se puede medir**.
  Solo Brier y ROI hacia adelante.
- Con 2632 partidos el Brier 0.210 es bueno, pero **no demuestra** que se bata
  al mercado: eso lo dirá `backtest.py` con ≥100 picks resueltos.
- El modelo **solo precio** ganador, hándicap, total y total por equipo. Props
  de jugador, cuartos/mitades, "carrera a N puntos" y márgenes de victoria se
  descartan (no se inventan).

