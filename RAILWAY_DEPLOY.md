# Despliegue en Railway (el hosting oficial desde ahora)

Decision tomada: el backend sale de **Northflank** y se queda en **Railway**.
Este es el documento de referencia para desplegar. `MIGRACION_HOSTING.md` queda
como analisis historico y checklist para apagar la cuenta vieja.

Lo que **no** se mueve (sigue igual): MySQL en **Aiven**, el frontend en
**Vercel**, el repo de **GitHub** y el reproductor TV (el `Dockerfile` ya trae
ffmpeg).

---

## 1. Que hay en el repo para Railway

| Archivo | Para que sirve |
|---|---|
| `railway.json` | Config as code: builder Dockerfile, 1 replica, sin sleep, healthcheck |
| `Dockerfile` | Imagen con ffmpeg y un `CMD` que respeta la variable `PORT` |
| `.dockerignore` | Imagen mas liviana y **sin `.env`** dentro del registry |
| `env_para_railway.py` | Convierte `.env` / `.env.northflank` en un bloque pegable |
| `exportar_env_northflank.py` | Baja las variables reales de Northflank (antes de apagarlo) |
| `test_env_para_railway.py` | Prueba offline del conversor (sin red ni secretos) |

### 1.1 Que significa cada linea de `railway.json`

```
build.builder = DOCKERFILE        -> usa TU Dockerfile (no Railpack ni Nixpacks)
build.dockerfilePath              -> esta en la raiz del repo
deploy.numReplicas = 1            -> UNA sola instancia (ver seccion 7: schedulers en memoria)
deploy.sleepApplication = false   -> nunca duerme: si duerme, no hay picks nuevos
deploy.restartPolicyType = ALWAYS -> si el contenedor muere, Railway lo levanta
deploy.healthcheckPath = "/"      -> ver seccion 5 (por que NO `/health`)
deploy.healthcheckTimeout = 300   -> margen para arrancar (deps, hilos, Aiven)
deploy.overlapSeconds = 0         -> sin solape: dos instancias = dos schedulers
deploy.drainingSeconds = 20       -> margen para cerrar sesiones de ffmpeg (SIGTERM)
```

> **Ojo:** Railway lee `railway.json` del **commit desplegado**. Si no lo subes a
> GitHub (`git push` a `main`), Railway no lo aplica aunque este en tu disco.

---

## 2. Antes de tocar nada

- `git status` limpio y **push a `main`** (Railway despliega de la rama `main`).
- El `.env` real a mano: esta en `.gitignore`, **nunca** se commitea.
- No borres Northflank todavia: es el respaldo si algo falla (seccion 8).

---

## 3. Paso a paso

### 3.1 Crear el proyecto

1. `railway.app` -> **New Project** -> **Deploy from GitHub repo** ->
   `Alecxzzz/3SIXTYBETS_AI_HOSTING`.
2. Settings -> **Build**: debe decir **Dockerfile**. Si dice Railpack/Nixpacks es
   que `railway.json` no esta en el commit desplegado.
3. Settings -> Networking -> **Generate Domain**. Apunta la URL
   (`https://<algo>.up.railway.app`): sera la `PUBLIC_BASE_URL` nueva.

### 3.2 Cargar las variables de entorno

Las variables **no estan en el repo**: viven en el panel de Northflank. Se pasan
en 3 comandos:

```powershell
# 1. Bajarlas de Northflank (aun vivo). Token: Account settings -> API tokens
#    con permisos Project > Secrets > Read y Services > General > Read.
$env:NORTHFLANK_API_TOKEN = "nf_xxx"
python exportar_env_northflank.py --project <tu-proyecto> --listar
python exportar_env_northflank.py --project <tu-proyecto> --service <servicio>
#    -> genera .env.northflank (ignorado por git)

# 2. Convertirlas al formato que Railway entiende
python env_para_railway.py
#    -> genera .env.railway listo para pegar, y avisa de lo que falte:
#       variables obligatorias (MYSQL_*, ADMIN_*, CHAT_CONV_SECRET,
#       FRONTEND_ORIGINS), valores que parecen plantilla y las que no se
#       pueden pegar tal cual (multilinea o con " #", que se cortarian).
```

3. Railway -> servicio -> **Variables** -> **Raw Editor** -> pegar el contenido
   completo de `.env.railway` -> **Deploy**.
4. Encima de lo pegado, **a mano**, estas tres (cambian respecto a Northflank):

```
PUBLIC_BASE_URL=https://<tu-dominio>.up.railway.app
FRONTEND_ORIGINS=https://threesixtybets-chat.vercel.app,https://threesixtybets.com
CHAT_CONV_SECRET=<el MISMO string largo que ya tenias>
```

- `CHAT_CONV_SECRET` **debe ser el mismo valor**: si lo cambias, todos los
  navegadores con una conversacion abierta reciben `403 Conversacion expirada`.
- **NO definas `PORT`**: Railway la inyecta y enruta el trafico a esa variable.
  Si la fijas a mano el servicio arranca pero responde
  `Application failed to respond` (el conversor la descarta por eso).
- `EMAIL_PROVIDER`, `EMAIL_FROM`, `RESEND_API_KEY`/`BREVO_API_KEY`, `SMTP_*` y
  `API_FOOTBALL_KEY` suelen estar **solo en el panel de Northflank**: si faltan
  en tu `.env` local, el paso 1 es el que las saca a la luz.

### 3.3 Primer arranque

- Deployments -> **View Logs**: hay que ver `Uvicorn running on
  http://0.0.0.0:<puerto>` y la conexion a Aiven. Con `PYTHONUNBUFFERED=1` (ya
  esta en el `Dockerfile`) los `print()` salen en vivo.
- Comprobaciones rapidas:

```powershell
curl https://<dominio>.up.railway.app/health     # db_ok: true, admin_configured: true
curl https://<dominio>.up.railway.app/props      # JSON con picks, no vacio
ffmpeg -version                                  # desde el shell del servicio (Railway -> Shell)
```

### 3.4 Aiven: permitir el trafico del host nuevo

Consola Aiven -> servicio MySQL -> **Allowed IP addresses**.

- Si el filtro esta vacio o en `0.0.0.0/0`: no hay nada que hacer.
- Si tiene IPs concretas (las de salida de Northflank), Railway **no** da IP de
  salida fija por defecto: o abres el rango, o revisas la opcion de *Static
  Outbound IPs* de Railway, o te quedas en un VPS (ultima opcion, seccion 9).
- **Si Aiven bloquea y no lo notas**, el sintoma es: `/health` devuelve
  `db_ok: false` tras ~100 s (5 reintentos x `connect_timeout=20`) y los picks
  no cargan. Por eso el healthcheck de Railway NO usa `/health` (seccion 5).

### 3.5 Frontend (Vercel)

- Vercel -> proyecto -> Settings -> Environment Variables: cambia la URL del
  backend (`VITE_API_BASE` / `NEXT_PUBLIC_API_URL`, segun como este montado) al
  dominio nuevo.
- **Redeploy**: las variables de Vercel solo se aplican en un build nuevo.
- Si el reproductor TV se sirve desde otro origen, pon el dominio nuevo en
  `CDN_API_BASE` de `channels.js` (hoy esta vacio = mismo dominio).

### 3.6 Pagadito (pagos)

- Si hay **URL de retorno/webhook** registrada, cambiala al dominio nuevo y
  revisa `PAGADITO_RETURN_REDIRECT`.
- Si Pagadito tiene lista blanca de IPs, ver seccion 3.4: Railway no da IP fija.

---

## 4. Verificacion post-migracion

```
[ ] GET /health                        -> 200, db_ok:true, admin_configured:true
[ ] GET /props                         -> JSON con picks (no lista vacia)
[ ] Dashboard carga partidos del dia
[ ] Chat del frontend responde (CORS: revisa FRONTEND_ORIGINS)
[ ] Recuperacion de contrasena arma el enlace con el dominio NUEVO
[ ] Reproductor TV: un canal por proxy y otro forzando /live (ffmpeg)
[ ] Login de admin funciona (ADMIN_USERNAME / ADMIN_PASSWORD)
[ ] Log sin warnings de variables faltantes
[ ] Tras 30 min: siguen apareciendo picks nuevos (los hilos de fondo viven)
[ ] Metrics de Railway: RAM por debajo del limite y sin reinicios
```

---

## 5. Por que el healthcheck es `/` y no `/health`

`/health` es la comprobacion **profunda** (consulta MySQL). Pero
`db.health_status()` llama a `run_query("select 1")`, que reintenta **5 veces**
con `connect_timeout=20`: si Aiven no responde, ese endpoint tarda hasta ~100 s.

Si Railway usa `/health` como healthcheck:

- un corte de Aiven (o la IP aun no permitida) hace que el deploy se marque
  como fallido y Railway lo revierte a la version anterior;
- un pico de latencia puede reiniciar un contenedor que estaba bien (y al
  reiniciar se pierden las caches en memoria de la TV).

`/` devuelve `Test` al instante, sin tocar la BD: confirma que **el proceso
escucha en `$PORT`**, que es lo unico que Railway necesita saber para enrutar.
La salud real de la BD se mira a mano con `/health` (o con `/dashboard`).

---

## 6. Recursos: RAM, CPU y factura (lo que cuesta de verdad)

Railway cobra **por uso** (vCPU y GB de RAM por minuto). Lo que importa aqui:

| Pieza del codigo | Consumo | Que hacer |
|---|---|---|
| `_SEGMENT_CACHE_MAX = 400` (`main.py`) | Guarda segmentos `.ts` en RAM: el mayor consumidor de memoria | Si el contenedor se reinicia solo por memoria, **baja ese numero** o sube la RAM del servicio |
| `MAX_TRANSCODER_SESIONES` (default **4**, `transcoder.py`) | Cada sesion de `/live` lanza un **ffmpeg real** -> CPU facturable | Con 2 o 3 espectadores simultaneos sobra. Bajalo a `2` desde las Variables |
| Hilos de fondo (`event_scheduler`, `props_scheduler`, `sports_warming`) | CPU constante, trafico cero | Es justo lo que **no** puede dormir: `sleepApplication=false` |

- Con **1 GB** de RAM va, pero si el reproductor se usa mucho, sube a **2 GB**.
- Mira **Metrics** en el panel el primer dia: si la CPU esta plana arriba, es el
  ffmpeg de la TV (normal si hay gente viendo) o una generacion de picks en
  bucle.
- Plan **Hobby** (~5 USD/mes) incluye credito de uso; si te pasas, se factura
  aparte. Pon un **usage limit** en Billing para no llevarte sorpresas.

---

## 7. Una sola instancia (no escales replicas)

`numReplicas` esta fijado en **1** en `railway.json` a proposito:

- los schedulers (`props_scheduler.py`, `event_scheduler.py`) generarian los
  mismos picks dos veces y gastarian el doble de llamadas a las APIs de IA;
- `_SEGMENT_CACHE_MAX` y las sesiones de transcode viven **en memoria** del
  proceso: cada instancia tendria su propia cache y el reproductor fallaria de
  forma intermitente (una peticion va a una instancia que no tiene el segmento).

El chat tolera multi-instancia (usa HMAC con `CHAT_CONV_SECRET`), pero eso no
justifica escalar: si necesitas aguantar mas trafico, primero sube RAM/CPU.

---

## 8. Apagar Northflank (en este orden)

1. El servicio en Railway lleva **2-3 dias** sirviendo `/health` y `/props` sin
   caidas y generando picks solos.
2. Vercel ya apunta al dominio nuevo y el frontend funciona.
3. En Northflank: **Pause / Suspend** el servicio (no lo borres). Conserva la
   configuracion y los secret groups: si algo falla, reanudas en un clic.
4. A los 2-3 dias sin problemas: **borra el servicio**. La **cuenta** es el
   ultimo clic (irreversible, se lleva builds, logs y tokens).
5. Guarda `.env.northflank` fuera del repo (gestor de contrasenas) **antes** de
   borrar la cuenta: los tokens de la API dejan de existir.

Checklist completa y que se destruye exactamente: `MIGRACION_HOSTING.md` seccion 7.

> No dejes los **dos** hosts generando picks mucho tiempo: escriben en la misma
> BD de Aiven y duplican llamadas a las APIs de IA (coste real).

---

## 9. Cuando Railway no basta

Si el reproductor TV con ffmpeg se come la factura o necesitas **IP de salida
fija** (Pagadito / firewall de Aiven), la salida natural es un **VPS** con
Docker + Caddy (~4 EUR/mes en Hetzner). Todo lo de este repo sirve igual: el
`Dockerfile` y las variables son los mismos; solo cambia quien pone TLS y el
reinicio. Detalle comparativo en `MIGRACION_HOSTING.md` seccion 3.

---

## 10. Trampas conocidas (por que fallaria)

- **`Application failed to respond`**: la app no escucha en `$PORT`. Ya esta
  resuelto en el `Dockerfile` (`--port ${PORT:-8080}`); revisa que no hayas
  definido `PORT` a mano en las Variables.
- **El deploy "crashea" sin logs utiles**: mira si el healthcheck apunta a
  `/health` en vez de `/` (seccion 5). Con `/health` un corte de Aiven tumba el deploy.
- **TV cae de golpe, sin error claro**: memoria -> `_SEGMENT_CACHE_MAX`. Sube RAM
  o baja el limite de cache.
- **"la IA no analiza" a los 10-20 min**: `sleepApplication` activado (serverless)
  o `numReplicas` > 1. El servicio debe estar siempre encendido y solo.
- **`403` en `/chat`**: `CHAT_CONV_SECRET` distinto o ausente (debe ser el mismo
  string que en Northflank).
- **CORS bloqueado en el navegador**: falta el dominio nuevo en `FRONTEND_ORIGINS`.
- **Enlaces de correo con `http://`**: falta `PUBLIC_BASE_URL`.
- **Variables con comillas o comentarios**: `EMAIL_FROM="3SIXTYBETS <x@y>"` pegado
  a mano guarda las comillas y los correos salen con remitente invalido. Usa
  siempre `env_para_railway.py`.
- **Imagen sin `ffmpeg`**: solo si alguien cambia el `Dockerfile` a Railpack. El
  builder debe ser **Dockerfile** y no tocar la linea de `apt-get ... ffmpeg`.
- **`railway.json` ignorado**: no esta en el commit desplegado, o el servicio
  esta configurado como "Image" (registry) en vez de repo de GitHub.
- **Deploy lento / build cache**: el `pip install` se cachea entre deploys; si
  cambias `requirements.txt` el build se rehace solo.

---

## 11. Comandos utiles (CLI de Railway, opcional)

```powershell
npm i -g @railway/cli      # o: winget install Railway.CLI
railway login
railway link               # elige proyecto + servicio (una vez)
railway status             # que esta desplegado
railway logs               # logs en vivo del contenedor
railway redeploy           # redeploy sin cambiar commit
railway variables          # lista las variables del servicio

# Cargar las variables de una en una desde el bloque generado
Get-Content .env.railway | ForEach-Object { railway variables --set $_ }
```

> El comando masivo va **una variable por llamada** (es lento: ~60 variables),
> pero sirve para rehacer el entorno en un servicio nuevo sin copiar y pegar.
> Si algo falla, el Raw Editor del panel siempre funciona.

---

## 12. Diferencias que si importan respecto a Northflank

| Tema | Northflank | Railway |
|---|---|---|
| Despliegue | Build desde GitHub | Igual, pero **lee `railway.json`** del commit |
| Variables | Panel + *secret groups* | Panel (Raw Editor para pegar en bloque) |
| IP de salida fija | La tenias | **No** por defecto (seccion 3.4) |
| Sleep por inactividad | No | Existe (serverless), **desactivado** aqui |
| Volumen persistente | No habia | No se usa: `/tmp` es efimero igual que antes |
| Rollback | Si | Si (Deployments -> Redeploy de una version anterior) |
| Facturacion | Precio fijo por plan | **Por uso**: vigila Metrics los primeros dias |



