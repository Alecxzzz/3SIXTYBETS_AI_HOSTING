# Implementation Plan

Motor de decisión estadística para picks de fútbol: sustituye la intuición de la IA por probabilidades calculadas a partir de datos reales de corners, tarjetas y árbitro, y un modelo que aprende de los errores de los picks ya registrados.

## Overview

Hoy el sistema pide a un modelo de lenguaje que elija un mercado basándose en su última forma (5 partidos). Eso produce aciertos indistinguibles del azar: sobre 369 picks resueltos el acierto global es 52,3% con cuota media 1,70, es decir **ROI −11,1%**. El plan añade el contexto que el prompt actual nunca tuvo — corners por equipo (global/local/visitante), tarjetas y el árbitro del partido — lo convierte en features numéricas, y entrena un clasificador que estima la probabilidad real de acierto de cada apuesta. Cuando esa probabilidad no supera el margen implícito en la cuota (`1/cuota`), se busca un mercado alternativo del mismo partido en lugar de publicar la apuesta original.

Alcance: **solo fútbol (`sport='soccer'`)**. MLB/NBA/tenis quedan fuera porque la fuente (FotMob) y el tipo de mercado analizados son de fútbol, y porque la muestra entrenable de NBA es de 2 partidos.

Contexto verificado en la investigación:
- FotMob `matchDetails` expone `corners`, `yellow_cards`, `red_cards`, `fouls` y `Referee` (con `id` numérico) — confirmado en `content.stats.Periods.All` y `content.matchFacts.infoBox`.
- FotMob `teams?id=` devuelve `fixtures.allFixtures` con 53 partidos de historia reciente — evita recorrer días uno a uno.
- FotMob `search/suggest` resuelve nombre de equipo → id (Arsenal → 9825), ya usado en `fotmob.py`.
- `ai_picks` tiene **224 picks de fútbol resueltos** (ACIERTO/FALLO) y **736 totales** en 4 deportes.
- No existe ninguna tabla de estadísticas: hay que crearla.

## Types

Nuevo módulo `backend/apuestas/tipos.py`:

```python
from dataclasses import dataclass, field

@dataclass
class StatPartido:
    """Un partido ya terminado, con las stats que necesita el motor."""
    match_id: int
    fecha: str                  # ISO date
    competition: str            # id de competencia de FotMob
    home_id: int
    away_id: int
    local: bool                 # True si el equipo analizado es el local
    goles_local: int
    goles_visitante: int
    corners_total: int
    corners_local: int          # corners a favor del equipo analizado
    yellow_total: int
    yellow_local: int
    referee_id: int | None
    referee_name: str | None

@dataclass
class PerfilEquipo:
    """Agregado de los últimos N partidos, separado local/visitante."""
    equipo_id: int
    nombre: str
    n: int
    corners_por_partido: float | None
    yellow_por_partido: float | None
    goles_por_partido: float | None
    corners_local: float | None
    corners_visitante: float | None
    yellow_local: float | None
    yellow_visitante: float | None
    goles_local: float | None
    goles_visitante: float | None
    n_local: int = 0
    n_visitante: int = 0
    fecha_ultimo: str | None = None

@dataclass
class FeaturesPick:
### Archivos nuevos

- **`backend/apuestas/__init__.py`** — paquete vacío.
- **`backend/apuestas/tipos.py`** — dataclasses de arriba.
- **`backend/apuestas/fotmob_stats.py`** — extracción de stats de FotMob, con caché en BD para no repetir requests.
- **`backend/apuestas/mercado.py`** — mapeo texto-del-bookmaker → `market_key`, extracción de línea y parsing de "Over 8.5".
- **`backend/apuestas/features.py`** — construcción del vector de features desde `PerfilEquipo` + features del partido.
- **`backend/apuestas/modelo.py`** — definición, entrenamiento, guardado y carga del clasificador.
- **`backend/apuestas/motor.py`** — orquestación: dado un partido, puntúa cada mercado y devuelve `Decision`.
- **`backend/apuestas/backtest.py`** — validación walk-forward (obligatoria antes de producción).
- **`backfill_stats.py`** (raíz) — script standalone para poblar la caché con los 224 picks históricos.
- **`tests/test_apuestas_motor.py`** — tests del módulo.
- **`tests/test_backtest_walkforward.py`** — tests de la validación.

### Archivos a modificar

- **`db.py`** — añadir helpers de persistencia: `crear_tabla_stats_cache()`, `guardar_stat_partido()`, `obtener_stats_equipo()`, `guardar_perfil()`. Seguir el patrón `create table if not exists` que ya usa el archivo.
- **`dashboard.py`** — tres cambios en `generar_picks_dia()`:
  1. Antes de `_preguntar_ia()`, invocar `motor.decidir()` con los mercados reales que ya devuelve `cuotas_doradobet.mercados_reales()`.
  2. Si `Decision.accion == "APOSTAR"`, forzar ese mercado/selección en el mensaje a la IA (la IA redacta la `rationale`, no elige).
  3. Si `Decision.accion == "DESCARTAR"`, pasar la mejor alternativa al re-análisis en vez de reintentar el mercado original.
- **`dashboard.py`** — nuevas columnas en el resumen que ya retorna la función: `decisiones_apuesta`, `decisiones_descarte`, `decisiones_sin_datos`.
- **`cuotas_doradobet.py`** y **`fotmob.py`** — no se modifican; el motor reutiliza sus helpers (`_linea_de`, `_get`, `_repara`, `_clave`, `buscar_equipo`) para no arriesgar `probabilidad.py`.

## Functions

### `backend/apuestas/fotmob_stats.py` (nuevas)

- `stats_partido(match_id: int) -> StatPartido | None`
  Llama `matchDetails?matchId=`. Extrae de `content.stats.Periods.All.stats[]` las claves `corners`, `yellow_cards`, `red_cards` (sumando rojas a amarillas) y de `content.matchFacts.infoBox["Referee"]` el `id` y `text`. Determina `local` comparando con `general.homeTeam.id`.

- `historial_equipo(equipo_id: int, n: int = 10, dias_atras: int = 60) -> list[StatPartido]`
  Descarga `teams?id=` **una vez**, usa `fixtures.allFixtures.fixtures`, filtra terminados y para los `n` más recientes llama `stats_partido()`. Si un `matchDetails` falla se salta ese partido — la pérdida de muestras se tolera, no se inventa.

- `perfil_equipo(equipo_id: int, nombre: str, n: int = 10) -> PerfilEquipo`
  Agrega el historial en tres cortes: todos, solo local, solo visitante. Si `n_local < 3` o `n_visitante < 3`, ese promedio queda en `None` en vez de dividir por una muestra ridícula.

- `perfil_arbitro(referee_id: int, league_id: int, n: int = 15) -> tuple[float|None, float|None]`
  Devuelve `(promedio_tarjetas, desviacion)`. **Es la función más cara** — ver Riesgos.

- `_clave_cache(match_id)` / `_guardar_cache()` / `_leer_cache()`
  Tabla `apuestas_stats_cache`, clave `f"m:{match_id}"`, columnas `payload JSON` + `ts`, TTL 30 días. El histórico de un partido no cambia jamás.

### `backend/apuestas/mercado.py` (nuevas)

- `market_key(texto: str) -> str | None`
  Normaliza el texto del bookmaker a clave canónica. Devuelve `None` para mercados no soportados (props de jugador, hándicap) — el motor no los evalúa y no se ven afectados.
- `linea_de(texto: str) -> float | None` — reutiliza `cuotas_doradobet._linea_de()`.
- `seleccion_de(texto: str) -> tuple[str, str]` — `(lado, umbral)`, lado ∈ `{"total","local","visitante","over","under"}`.

### `backend/apuestas/features.py` (nuevas)

- `esperar_corners(p_local: PerfilEquipo, p_visit: PerfilEquipo) -> float | None`
  **El núcleo de la lógica pedida:**
  ```
  corners_local_esperados     = p_local.corners_local      # lo que genera en casa
  corners_visitante_esperados = p_visit.corners_visitante  # lo que genera fuera
  corners_total_esperado      = suma de ambos
  ```
  Si falta el corte local/visitante, se degrada al promedio global **con 15% menos de confianza**, en vez de asumir el global.

- `esperar_amarillas(p_local, p_visit, referee) -> float | None`
  Igual, pero sumando el efecto del árbitro: `base * (1 + z_arbitro)` con `z_arbitro = (promedio_arbitro - media_liga) / desviacion_liga`, **acotado a `[-0.5, +0.5]`** para que un árbitro extremo no domine la proyección.

- `features_de_pick(mercado, perfil_local, perfil_visit, referee, n_muestra) -> FeaturesPick`
  Calcula el **porcentaje histórico real**: qué fracción de los últimos N partidos del local superó esa línea. Es el dato que la IA hoy no tiene.

- `es_confiable(f) -> bool` — `False` si `sample_size < 5`; el motor devuelve `SIN_DATOS` en vez de inventar probabilidad.

### `backend/apuestas/modelo.py` (nuevas)

- `FEATURES_NUMERICAS = ["cuota","corners_pct_historico","corners_linea","yellow_pct_historico","yellow_linea","referee_card_promedio","goles_esperado","sample_size"]`
### `backend/apuestas/motor.py` (nuevas)

- `UMBRAL_PROB_MINIMA = 0.55` — calibrada por el backtest.
- `decidir(p: dict, mercados: list[dict]) -> Decision`
  Resolver perfiles → perfil del árbitro → por cada mercado del bookmaker construir features → predecir → calcular `valor_esperado = prob*(cuota-1) - (1-prob)` → ordenar → devolver la mejor por encima del umbral. Si todas quedan debajo: `accion="DESCARTAR"` con `alternativas` = top-3 para que el usuario vea las opciones.

### `backend/apuestas/backtest.py` (nuevas)

- `walk_forward(n_splits: int = 5) -> dict`
  **Split temporal por fecha, nunca aleatorio.** Por split: entrenar con los anteriores a la fecha de corte, predecir los posteriores. Devuelve ROI, acierto y curva.
- `reporte() -> dict` — formato imprimible.

## Dependencies

- **`scikit-learn>=1.3`** — ya instalada en `.venv` durante la investigación; entrar en `requirements.txt`.
- **`joblib`** — dependencia de scikit-learn; serializa el pipeline.
- **Sin dependencias nuevas de red**: FotMob se consume con `requests`, ya presente.

No se añade ningún SDK de OpenAI/Anthropic. El modelo es local y no cuesta tokens.

## Testing

### `tests/test_apuestas_motor.py`
- `test_market_key_reconoce_variantes`: "Total tiros de esquina, minimo 8.5" → `corners_total_over`; "hándicap" → `None`.
- `test_esperar_corners_suma_local_mas_visitante`: perfiles sintéticos (local 8.7, visitante 7.2) → 15.9.
- `test_perfil_degrada_sin_muestra`: con `n_local=2` el promedio queda `None`, no una división por 2.
- `test_arbitro_acotado`: árbitro con `z=4` se acota a `+0.5`.
- `test_sin_datos_no_inventa_probabilidad`: `sample_size=3` → `Decision.accion == "SIN_DATOS"`.
- `test_valor_esperado_descarta_cuota_baja`: prob 0.55 a cuota 1.20 → EV negativo → `DESCARTAR`.

### `tests/test_backtest_walkforward.py`
- `test_split_es_temporal`: los índices de train son siempre anteriores a los de test.
- `test_no_hay_fuga_de_datos`: el scaler se ajusta solo con train. **Este test protege contra sobreajuste y no debe omitirse.**

### Fuera de alcance
`test_contexto_avisa_cuando_no_encuentra_el_partido` (en `tests/test_you_integration.py`) ya fallaba antes de estos cambios — verificado con `git stash`. No es regresión.

## Implementation Order

1. **Persistencia en `db.py`** — `apuestas_stats_cache`, `apuestas_perfil`. Verificar con `INSERT`/`SELECT` real contra Aiven.
2. **`fotmob_stats.py`** — `stats_partido()` verificado contra el partido real de la investigación (corners 3-4, 1 amarilla por equipo, árbitro "Szymon Marciniak" id 1001073072). Luego `historial_equipo()` y `perfil_equipo()`.
3. **`mercado.py`** — puro, sin red. El que más casos de borde tiene.
4. **`features.py`** — la lógica de proyección, con perfiles sintéticos y sin red.
5. **`backfill_stats.py`** — poblar la caché de los **224 picks de fútbol resueltos**. Tarda minutos. **Punto de control: si menos de ~150 partidos devuelven stats usables, parar y replantear** — significa que la cobertura de FotMob para esas ligas es insuficiente.
6. **`modelo.py` + `backtest.py`** — **Punto de control obligatorio: el backtest debe mostrar ROI positivo en al menos 3 de 5 splits.** Si no, el modelo no entra en producción y se reporta al usuario con los números en crudo.
7. **`motor.py`** — integrar con `cuotas_doradobet.mercados_reales()`. Devolver `Decision` **sin cambiar todavía el comportamiento de publicación**.
8. **Integración en `dashboard.py`** — la IA pasa a redactar el `rationale` del mercado que elige el motor, detrás de un flag `MOTOR_APUESTAS=1` para poder apagar sin redesplegar.
9. **Backtest en vivo** — comparar 2 semanas de picks con motor contra los que habría generado la IA sola. Es la única validación honesta.

## Riesgos y límites conocidos

- **224 muestras son pocas.** Un modelo con más de ~10 features struggle con esa cantidad. Por eso se limitan a 8 numéricas + 1 categórica y se usa regresión logística. Si el backtest falla, la respuesta correcta es **acumular más datos**, no añadir features.
- **Sobreajuste.** Por eso el paso 6 es bloqueante y el split es temporal. Con `n=224` un modelo puede dar +40% en train y −20% en test; **solo importa el segundo número**.
- **Coste de `perfil_arbitro()`.** En la investigación, 12 llamadas a `/matches` llevaron 8,1s (0,68s cada una). Enumerar una liga para encontrar 15 partidos del mismo árbitro puede requerir cientos de llamadas. **Mitigación: caché 30 días y cálculo diferido**; si supera `ARBITRO_MAX_LLAMADAS=120`, devuelve `None` y el motor funciona sin ese factor (degradación limpia, no fallo).
- **Cobertura de FotMob.** Las ligas menores pueden no tener `matchDetails` con stats. El motor degrada a `SIN_DATOS`, nunca a un número inventado.
- **Este plan no arregla el sesgo de las cuotas altas.** La franja 2.2–9.0 rinde +90,7% y las bajas pierden. Revisar `ODDS_MINIMA`/`ODDS_MAXIMA` (`dashboard.py:30-31`, hoy 1.20–2.50) es decisión del usuario, no parte de este plan.

- `FEATURES_CATEGORICAS = ["market_key"]`
- `entrenar(datos: list[dict]) -> Pipeline`
  `ColumnTransformer` con `OneHotEncoder` para `market_key` + `StandardScaler` para numéricas, sobre `LogisticRegression(class_weight="balanced", max_iter=1000)`. **Regresión logística, no un árbol**, porque con 224 filas un árbol sobreajusta de inmediato y la salida debe ser interpretable (el usuario verá por qué se apuesta).
- `guardar(pipeline, metricas)` / `cargar() -> Pipeline | None`
  `apuestas_modelo.pkl` + `apuestas_modelo_meta.json` con fecha, `n_datos`, accuracy, roc_auc, coeficientes.
- `predecir_probabilidad(pipeline, features) -> float | None`
  `None` si `market_key` es categoría no vista en entrenamiento.

    """Vector de features para un pick candidato."""
    market_key: str             # clave canónica del mercado
    market_raw: str             # texto original, para auditoría
    cuota: float
    corners_esperado: float | None
    corners_linea: float | None
    corners_pct_historico: float | None  # % de partidos del local que superaron la línea
    yellow_esperado: float | None
    yellow_linea: float | None
    yellow_pct_historico: float | None
    referee_card_promedio: float | None
    referee_tarjeta_desv: float | None   # cuánto se desvía de la media de la liga
    goles_esperado: float | None
    sample_size: int = 0        # si es bajo, no se afirma nada
    def como_dict(self) -> dict: ...

@dataclass
class Decision:
    """Salida del motor: qué apostar, o por qué no apostar nada."""
    accion: str                 # "APOSTAR" | "DESCARTAR" | "SIN_DATOS"
    market: str | None
    cuota: float | None
    probabilidad: float | None
    valor_esperado: float | None  # prob*(cuota-1) - (1-prob)
    motivo: str
    alternativas: list = field(default_factory=list)
```

Mercados soportados en la primera versión (`market_key` canónico):
- `corners_total_over`, `corners_total_under`
- `corners_local_over`, `corners_visitante_over`
- `yellow_total_over`
- `goles_total_over`, `goles_local_over`
