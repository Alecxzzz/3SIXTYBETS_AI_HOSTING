# Migrar el backend de Northflank a otro hosting

> **Estado (decision tomada):** el host nuevo es **Railway**. La guia operativa
> de despliegue vive en **`RAILWAY_DEPLOY.md`** (config `railway.json`, variables,
> healthcheck, apagado de Northflank). Este documento queda como analisis del
> inventario y como checklist del borrado definitivo de Northflank (seccion 7).

Guia operativa para mover 3SIXTYBETS fuera de Northflank sin downtime y sin
perder datos.

---

## 1. Diagnostico: que hay que mover y que NO

Revisando el repo (`Dockerfile`, `.env`, `db.py`, `transcoder.py`) el inventario
real es este:

| Pieza | Donde vive | Hay que migrarla? |
|---|---|---|
| Backend FastAPI (`main.py`) | Northflank (contenedor Docker) | **SI** - es lo unico que se mueve |
| Base de datos MySQL | **Aiven** (`mysql-...sixtydb.d.aivencloud.com`) | **NO** - es externa, sigue igual |
| Frontend / dashboard | Vercel (`threesixtybets-chat.vercel.app`) | NO - solo se le cambia la URL del backend |
| Archivos / volumen persistente | **No hay**. `transcoder.py` usa `tempfile.gettempdir()` (`/tmp`), que es efimero | NO |
| Imagen Docker | Se construye desde este repo en cada deploy (no hay registry propio) | NO - el host nuevo la reconstruye |

**Conclusion:** no hay datos que "extraer" de Northflank. No existe volumen, ni
base de datos gestionada por Northflank, ni storage. Migrar = **desplegar el
mismo repo en otro host, copiar las variables de entorno y apuntar el frontend a
la URL nueva.** Se puede hacer sin tiempo de caida (los dos hosts conviven unos
minutos).

---

## 2. Requisitos que el host nuevo TIENE que cumplir

Estos no son opcionales, salen del codigo:

1. **Servicio siempre encendido (no suspender por inactividad).**
   `main.py` arranca hilos de fondo (scheduler de picks, `props_scheduler.py`,
   `event_scheduler.py`, `sports_warming.py`). Si el host duerme el contenedor
   cuando no hay trafico, el dashboard deja de generar picks y nadie se entera.
   -> Descartar planes free que "duermen" (Render free, Koyeb free con sleep).
2. **Minimo 1 GB de RAM, recomendado 2 GB.**
   `_SEGMENT_CACHE_MAX = 400` en `main.py` guarda segmentos `.ts` en RAM; el
   propio codigo documenta que al pasarse del limite Northflank reiniciaba el
   contenedor y caia toda la TV. Con 512 MB no aguanta.
3. **CPU decente si usas el reproductor TV.**
   El endpoint `/live` lanza **FFmpeg** real (transcodifica a H.264). Un vCPU
   compartido muy flojo hace que el transcode no arranque en los 30s de
   `START_TIMEOUT`.
4. **Dockerfile** (ya existe y ya instala ffmpeg). No hace falta Nixpacks/buildpack.
5. **Puerto**: el Dockerfile ya es portable (`${PORT:-8080}`), asi que sirve tanto
   para hosts que fijan 8080 (Railway, Koyeb, Northflank) como para los que
   inyectan `PORT` (Render, Cloud Run).
6. **HTTPS publico** con dominio: obligatorio (el frontend en Vercel es https y
   el navegador bloquea mixed content; el reproductor ya depende de esto).

---

## 3. Opciones recomendadas

| Host | Coste aprox. | Pro | Contra |
|---|---|---|---|
| **Railway** | ~5 USD/mes (Hobby) | Deploy desde GitHub en 5 min, always-on, importa `.env` de golpe, logs buenos | RAM/CPU se cobran por uso; vigilar el gasto |
| **Fly.io** | ~5-10 USD/mes | Always-on real, regiones, muy estable | Hay que instalar `flyctl` y escribir `fly.toml`; ffmpeg consume CPU facturable |
| **Hetzner VPS (CX22)** | ~4 EUR/mes | Lo mas barato para transcode (2 vCPU dedicados), control total | Tu administras Docker, TLS (Caddy), reinicios |
| **Render (Starter)** | 7 USD/mes | Simple, Dockerfile directo | Arranque lento; free tier NO sirve (duerme) |
| **Koyeb** | ~5-7 USD/mes | Always-on, se puede desactivar el scale-to-zero | Menos RAM por defecto |

**Recomendacion corta:** si quieres el cambio con menos riesgo y en una tarde,
**Railway**. Si el reproductor de TV con FFmpeg te importa y quieres el coste
mas bajo a largo plazo, **Hetzner + Docker + Caddy**.

---

## 4. Paso a paso (ejemplo Railway, aplica igual en los demas)

### 4.1 Antes de tocar nada
- Confirma que el repo esta subido: `git status` limpio en `main`.
- Ten a mano el `.env` real (esta en `.gitignore`, **nunca se commitea**).

### 4.2 Crear el servicio
1. railway.app -> New Project -> **Deploy from GitHub repo** -> `Alecxzzz/3SIXTYBETS_AI_HOSTING`.
2. Railway detecta el `Dockerfile` solo. No cambies el builder.
3. Settings -> Networking -> **Generate Domain**. Apunta la URL: sera tu
   `PUBLIC_BASE_URL` nueva.

### 4.3 Cargar las variables de entorno

Tu Northflank tiene **dos cosas conectadas** y solo una hay que "copiar":

| Que | Como se migra |
|---|---|
| **Conexion con GitHub** | **No se migra**: es solo una integracion OAuth de la plataforma. En el host nuevo conectas el **mismo repo** (`Alecxzzz/3SIXTYBETS_AI_HOSTING`) y ya esta. El repo es la fuente de la verdad. Lo unico que debes replicar es *como* lo construye Northflank si lo cambiaste: rama (hoy `main`), ruta del Dockerfile (raiz), comando de arranque (ya esta en el Dockerfile) y build args si los hubiera. |
| **Variables de entorno** | **Si se copian**: viven en el panel de Northflank (runtime environment del servicio y/o *secret groups*). No estan en el repo (`.env` esta en `.gitignore` por seguridad). |

#### 4.3.1 Extraer las variables reales de Northflank (recomendado)

No confies en que tu `.env` local este al dia: si alguna key se agrego directamente
en el panel, se perderia sin avisar. El repo trae un script que las baja por la API:

```powershell
# 1. Crea un API token en Northflank -> Account settings -> API tokens
#    con permisos: Project > Secrets > Read  y  Project > Services > General > Read
$env:NORTHFLANK_API_TOKEN = "nf_xxx"

# 2. Ver los servicios y sus ids (el id del proyecto sale en la URL del panel)
python exportar_env_northflank.py --project <tu-proyecto> --listar

# 3. Exportar todo a .env.northflank
python exportar_env_northflank.py --project <tu-proyecto> --service <servicio>
```

El script:
- junta el runtime environment del servicio **y** los secret groups del proyecto
  (los `arguments`, que son build args, se omiten a proposito);
- si una variable esta en los dos sitios, gana la del servicio y te avisa;
- escribe `.env.northflank` con comillas solo donde hacen falta (validado contra
  `python-dotenv`), listo para pegar en Railway/Render/Fly o en un VPS;
- **te dice que variables existen en Northflank y NO en tu `.env` local** (las que
  habrias perdido) y cuales sobran en local.

`.env.northflank` esta en `.gitignore`: contiene secretos, no se sube.

> Nota del repo: en tu `.env` local hay 58 variables y ninguna vacia, pero faltan
> las de correo (`EMAIL_PROVIDER`, `EMAIL_FROM`, `RESEND_API_KEY`/`BREVO_API_KEY`/
> `SMTP_*`) y `API_FOOTBALL_KEY`. Si los correos de recuperacion funcionan en
> produccion, **estan solo en Northflank**: el script las va a sacar a la luz.

Ademas **define explicitamente** (no estaban o cambian):
```
PUBLIC_BASE_URL=https://<tu-dominio-nuevo>
FRONTEND_ORIGINS=https://threesixtybets-chat.vercel.app,https://threesixtybets.com
CHAT_CONV_SECRET=<el mismo string largo que ya tenias>
```
`CHAT_CONV_SECRET` **debe ser el mismo valor**: si lo cambias, todos los
navegadores con una conversacion abierta reciben 403 "Conversacion expirada".

Variables que ya tienes y que NO debes olvidar (lista completa en `.env`):
`MYSQL_*`, `ADMIN_*`, `GROQ_*`, `AI36_*`, `DASHBOARD_AI_*`, `ANALISTA_AI_*`,
`DEMIAN_AI_*`, `SUPPORT_AI_*`, `STATS_AI_*`, `API_FOOTBALL_KEY`,
`SPORTRADAR_API_KEY`, `PAGADITO_*`, `OPENROUTER_*`.

### 4.4 Primer arranque
- Mira el log hasta ver que uvicorn levanta y que conecta a Aiven.
- Prueba: `curl https://<dominio-nuevo>/health`
- Prueba datos reales: `curl https://<dominio-nuevo>/props` (debe devolver JSON
  con picks, no lista vacia).

### 4.5 Aiven: permitir la IP nueva (solo si tienes IP filter activo)
Consola Aiven -> servicio MySQL -> **Allowed IP addresses**. Si ahi hay IPs
concretas (por ejemplo las de salida de Northflank), agrega la IP de salida del
host nuevo o pon `0.0.0.0/0` temporalmente para probar. Si el filtro no esta
configurado, salta este paso.

### 4.6 Frontend / Vercel
En el proyecto de Vercel que consume la API:
- Actualiza la variable con la URL del backend (ej. `VITE_API_BASE` /
  `NEXT_PUBLIC_API_URL`, segun como este montado) al dominio nuevo.
- Redeploy (las variables de Vercel solo se aplican en un build nuevo).
- Si el reproductor de TV se sirve desde otro origen distinto al backend, pon el
  dominio nuevo en `CDN_API_BASE` de `channels.js` (hoy esta vacio porque asume
  mismo dominio).

### 4.7 Pagadito (pagos)
Si en el panel de Pagadito hay una **URL de retorno/webhook** registrada,
cambiala al dominio nuevo y verifica `PAGADITO_RETURN_REDIRECT`. Si Pagadito
tuviera lista blanca de IPs, agrega la del host nuevo (Northflank daba IP
estatica; Railway/Fly no por defecto -> si esto es requisito, usa un VPS).

### 4.8 Apagar Northflank (en este orden)
1. Host nuevo respondiendo `/health` y `/props` OK.
2. Dejar correr 1-2 horas con trafico real (los dos hosts escriben en la misma
   BD de Aiven, pero **no** dejes los dos schedulers generando picks mucho
   tiempo: duplicarian trabajo y llamadas a las APIs de IA).
3. En Northflank: **Pause / Suspend** el servicio (no lo borres todavia).
4. A los 2-3 dias sin problemas: borra el servicio para que deje de facturar.

---

## 5. Checklist de verificacion post-migracion

```
[ ] GET /health                        -> 200
[ ] GET /props                         -> JSON con picks (no vacio)
[ ] Dashboard web carga y muestra partidos del dia
[ ] Chat del frontend responde (CORS OK: revisa FRONTEND_ORIGINS)
[ ] Recuperacion de contrasena arma el enlace con el dominio NUEVO
[ ] Reproductor TV: un canal en modo proxy y otro forzando /live (ffmpeg)
[ ] Login de admin funciona (ADMIN_USERNAME / ADMIN_PASSWORD)
[ ] Log sin warnings de variables faltantes (ODDS_API_KEY, CHAT_CONV_SECRET...)
[ ] Tras 30 min: siguen apareciendo picks nuevos (los hilos de fondo viven)
```

---

## 6. Trampas conocidas (por que fallaria)

- **502 / "arranca pero no responde"**: el host espera otro puerto. Ya resuelto
  con `${PORT:-8080}` en el Dockerfile; si usas un host raro, exporta
  `PORT=8080`.
- **"la IA no analiza" a los 10 min**: el plan duerme el contenedor o lo reinicia
  por memoria. Sube RAM a 2 GB y baja `MAX_TRANSCODER_SESIONES` (default 4).
- **403 en /chat**: `CHAT_CONV_SECRET` distinto o ausente.
- **TV cae de golpe sin error claro**: limite de memoria -> `_SEGMENT_CACHE_MAX`.
- **CORS bloqueado en el navegador**: olvidaste agregar el dominio nuevo a
  `FRONTEND_ORIGINS`.
- **Enlaces de correo con `http://`**: falta `PUBLIC_BASE_URL` (detras de proxy
  el request llega como http).
- **No ejecutes 2 instancias** del contenedor al principio: hay caches en memoria
  (`_SEGMENT_CACHE_MAX`, sesiones de transcode). El codigo tolera multi-instancia
  para el chat (HMAC), pero el resto esta pensado para una sola.

---

## 7. Cuando borrar la cuenta de Northflank (y cuando NO)

**Regla de oro: la cuenta es el ULTIMO clic.** Es irreversible y no hay papelera.
El orden correcto es: host nuevo verificado -> pausar -> borrar el servicio ->
solo entonces la cuenta.

### 7.1 Que se destruye al borrar la cuenta
- Proyectos, servicios y **todo el historial de builds y logs** (tu unica fuente
  para comparar comportamiento si algo falla despues).
- Los **secret groups** y los **API tokens**: el token que usaste para exportar
  deja de existir. Guarda el `.env.northflank` en un gestor de contrasenas antes.
- La instalacion de la **GitHub App** en tu cuenta (no afecta al repo).
- Los dominios `*.northflank.app` / `*.code.run` dejan de resolver **al instante**.
- Cualquier **addon** de Northflank (bases de datos, Redis, buckets) si lo hubiera.

### 7.2 Que NO se destruye
- **Aiven MySQL** (verificado: tu `MYSQL_HOST` es `...aivencloud.com`, contrato
  aparte) -> tus datos siguen ahi.
- **Vercel** y **GitHub** -> intactos.
- Nada de tu `.env` local ni del repo.

### 7.3 Checklist antes de borrar
```
[ ] El host nuevo lleva 2-3 dias sirviendo /health y /props sin caidas
[ ] Los picks se siguen generando solos (hilos de fondo vivos)
[ ] Vercel ya apunta al dominio NUEVO y el frontend funciona
[ ] Probaste el reproductor TV (proxy + /live con ffmpeg)
[ ] Recuperacion de contrasena envia correo con el dominio nuevo
[ ] Si Pagadito tiene webhook/URL de retorno registrada, ya la cambiaste
[ ] Tienes .env.northflank guardado fuera del repo (gestor de contrasenas)
[ ] Northflank -> Billing -> Invoices: sin facturas pendientes. Borrar la
    cuenta NO cancela lo ya facturado.
[ ] Usaste "Suspend" y nada se rompio durante 24-48 h
```

### 7.4 Lo que SI puedes hacer ahora mismo (sin riesgo)
En el servicio: **Suspend / Pause**. Eso detiene el consumo y el cobro del
servicio pero conserva la configuracion, los secret groups y el token: si el
host nuevo falla, reanudas en un clic. Es el paso intermedio correcto despues de
migrar; el borrado definitivo, a los 2-3 dias.

### 7.5 Ojo con las URLs que viven fuera de este repo
El backend en Vercel suele llevar la URL del API en su propia variable de entorno
(no aparece en este repo). Mientras esa variable siga apuntando a
`*.northflank.app`, el frontend se cae **aunque el host nuevo este perfecto**.
Revisa Vercel -> Settings -> Environment Variables antes de suspender nada.


