# SEGURIDAD — nivel empresarial

Estado tras la auditoría y el endurecimiento. Cada control indica **dónde está**
y **cómo se verifica**, porque "tener seguridad" no es una afirmación: es un
conjunto de cosas comprobables.

---

## 1. Controles IMPLEMENTADOS (código)

| # | Control | Dónde | Cómo se verifica |
|---|---|---|---|
| 1 | **IP real resistente a falsificación** | `security.ip_del_cliente` | `test_seguridad.TestIPReal` (6 tests) |
| 2 | **Rate limit** por IP y por usuario | `security.limite` | `TestLimites` |
| 3 | **Bloqueo por CUENTA** tras 8 fallos (frena fuerza bruta distribuida) | `security.registrar_fallo` | `TestLimites` |
| 4 | **Login en tiempo constante** (hash ficticio si el usuario no existe) | `main.auth_signin` | revisión |
| 5 | **Sesiones hasheadas en BD** + migración sin echar a nadie | `db.create_session`, `db.get_user_by_token` | `TestSesionesHasheadas` (4 tests) |
| 6 | **Cookie HttpOnly + Secure + SameSite** además del Bearer | `security.poner_cookie_sesion` | `TestCookie` |
| 7 | **Token fuera de la URL** en la factura | `main.transaction_invoice` | revisión |
| 8 | **CSP** en HTML + HSTS + COOP + nosniff + no-store | `security.aplicar_cabeceras` | `TestCabeceras` (4 tests) |
| 9 | **CORS con lista blanca** (antes reflejaba cualquier origen) | `main.py` middleware | revisión |
| 10 | **Auditoría** de login, fallos, bloqueos, admin denegado, signup, logout | `db.security_events` | `select * from security_events` |
| 11 | **`/health` sin filtrar** si existe admin | `db.health_status` | `GET /health` |
| 12 | **Política de contraseña** (12 chars, letras+números, no comunes) | `security.validar_password` | `TestPasswords` |
| 13 | **Anti-SSRF** en HLS/FFmpeg | `main._url_tiene_host_publico` | ya existía |
| 14 | **Contraseñas PBKDF2-SHA256** con salt | `db.hash_password` | ya existía |
| 15 | **Rol admin validado en servidor** (nunca desde el cliente) | `main.get_admin` | ya existía |
| 16 | **IDOR controlado** en facturas (valida dueño) | `main.transaction_invoice` | ya existía |

**82 tests automáticos** cubren esto: `python -m unittest test_seguridad`.

---

## 1.b TODO PRIVADO POR DEFECTO (puerta global)

En vez de poner `Depends(get_current_user)` en 95 endpoints —y olvidarlo en el
siguiente que se escriba— la regla está **invertida**: si la ruta no está en la
lista blanca, exige sesión. Un endpoint nuevo **nace protegido**.

Implementado en `main._puerta_privada` (middleware, `API_PRIVADA=1` por defecto).

**Inventario real de la app: 95 rutas → 4 públicas / 91 privadas.**

### Las 4 públicas, y por qué cada una es imposible de cerrar

| Ruta | Por qué NO puede pedir sesión |
|---|---|
| `GET /health` | **La consulta el host, no una persona.** `railway.json` tiene `healthcheckPath: /health`: si devuelve 401, Railway marca el contenedor como caído y **entra en bucle de reinicio** (el servicio no llega a estar vivo). Devuelve `{ok, db_ok}`: ni datos de usuario, ni del admin, ni del negocio. |
| `POST /auth/signin` | Para tener sesión hay que llamar aquí. Si pide sesión, **nadie puede entrar nunca**. |
| `POST /auth/signup` | Igual: sin cuenta no hay sesión. |
| `GET /pagadito/return` | Pagadito redirige el navegador aquí tras el cobro. Si exige sesión y el usuario pagó desde otro dispositivo, **el pago no se acredita**. No expone nada: valida el ERN contra Pagadito. |

Las tres se compensan con lo que sí se puede endurecer: rate limit por IP,
bloqueo por cuenta, PBKDF2 de 120.000 iteraciones, auditoría de cada intento y
mensajes que no revelan si un usuario existe.

### Todo lo demás devuelve 401 `sesion_requerida`

Incluye lo que antes era público y ya no: `/`, `/models`, `/analytics/pv`,
`/errors/report`, `/tv/canales-cdn`, `/tv-live/*`, `/docs`, `/openapi.json`.

⚠️ **Recuperación de contraseña cerrada por defecto.** `/auth/forgot`,
`/auth/reset`, `/recuperar` y `/verificar-email` no pueden pedir sesión (el
usuario que olvidó su clave no la tiene), así que con `PERMITIR_RECUPERACION=0`
quedan cerradas. **Consecuencia real: quien olvide la contraseña no puede
recuperarla solo.** Se resetea desde el servidor:

```bash
python reset_password.py <usuario>
python reset_password.py --todos    # solo cierra todas las sesiones
```

Si prefieres que exista recuperación por email, `PERMITIR_RECUPERACION=1`
(y esas 4 rutas vuelven a ser públicas).

### Detalles que hacen que esto no rompa nada

- **OPTIONS pasa siempre**: el preflight de CORS no lleva credenciales por
  especificación; bloquearlo rompería toda llamada cross-origin.
- **El 401 lleva cabeceras CORS** propias (`_cors_para_401`) solo para orígenes
  de la lista blanca. Sin eso el navegador mostraría "error de red" en lugar de
  "sesión requerida".
- **Caché de 60 s** (`security.usuario_de_token`): validar en cada petición no
  añade una consulta a BD por request.

### Cómo añadir un endpoint

- Si es de negocio → **no hagas nada**, ya nace privado.
- Si tiene que ser público → añadirlo a `RUTAS_PUBLICAS` en `main.py` **con el
  motivo escrito al lado**. Esa lista es la superficie de ataque pública: cada
  línea debe estar justificada.

### Apagar la puerta (rollback)

`API_PRIVADA=0` en Railway → vuelve al modelo anterior. Sin tocar código.

```bash
SEC_COOKIE_NAME=sb_session
SEC_COOKIE_SAMESITE=none      # 'strict' cuando el frontend pase por rewrite same-origin
SEC_COOKIE_DIAS=30

TRUSTED_PROXY_HOPS=1          # Railway = 1. Con Cloudflare delante = 2
TRUST_CLOUDFLARE=0            # 1 si Cloudflare esta delante (usa CF-Connecting-IP)

SEC_FALLOS_BLOQUEO=8
SEC_BLOQUEO_MIN=15
SEC_PASSWORD_FUERTE=1

FRONTEND_ORIGINS=https://www.theesixtybets.xyz,http://localhost:5173
FRONTEND_ORIGINS_REGEX=^https://([a-z0-9-]+\.)*(threesixtybets\.com|theesixtybets\.xyz|3sixtybets\.com)$|^https://(threesixtybets|3sixtybets)(-[a-z0-9-]+)?\.vercel\.app$
```

Ese regex deja pasar **solo** los dominios reales del proyecto (incluidos los
previews de Vercel) y bloquea `https://evil.com`, `http://` y
`https://threesixtybets.evil.com` (verificado). Si añades un dominio nuevo hay
que añadirlo aquí o el navegador bloqueará las llamadas.

⚠️ **`TRUSTED_PROXY_HOPS` es crítico.** Si pones Cloudflare y lo dejas en 1, la
IP que se lee es la de Cloudflare: todos los usuarios compartirían el mismo
límite y el primero que abusara bloquearía a todos.

---

## 3. Lo que queda por hacer (y quién lo hace)

### 3.1 Tú, hoy — rotación de credenciales
Si alguien tuvo acceso al entorno, **ningún parche arregla una credencial
expuesta**. Orden:

1. `PAGADITO_WSK` → regenerar en el panel de Pagadito.
2. `MYSQL_PASSWORD` → cambiar en Aiven y en Railway.
3. `GROQ_API_KEY`, `DASHBOARD_AI_API_KEY`, `ANALISTA_AI_API_KEY`, `DEMIAN_AI_API_KEY`.
4. `CHAT_CONV_SECRET` → nuevo valor (`python -c "import secrets;print(secrets.token_hex(32))"`).
5. Contraseña del admin + **cerrar todas las sesiones**: `delete from sessions;`
6. Revisar **quién** tiene acceso al proyecto de Railway y al de Vercel.

### 3.2 Migración del frontend (necesita cambios en Vercel)
El backend ya acepta cookie HttpOnly, pero el frontend sigue guardando el token
en `localStorage`. Para eliminar ese riesgo de verdad:

1. `vercel.json` con rewrite `/api/:path*` → `https://<backend>.up.railway.app/:path*`.
2. `VITE_API_URL=/api` (mismo origen).
3. Quitar `localStorage.getItem("access_token")` de `frontend_dashboard/*.jsx`
   y las funciones `tk()` / `setTk()` de `extras.py` y `track_page.html`.
4. Poner `SEC_COOKIE_SAMESITE=strict` en Railway.
5. **Solo entonces** se puede exigir cookie y rechazar el Bearer.

Mientras no se haga, un XSS **sigue pudiendo** leer el token de localStorage.
La cookie ya se emite, pero el frontend todavía no la usa como camino principal.

### 3.3 Canales y streams — MECANISMO YA IMPLEMENTADO (falta encenderlo)

El problema no era "el video": es que la cadena
`listar canales → resolver stream → ver` era pública de punta a punta.

**Ya está construido** (`main._exigir_acceso_tv`, activado con `TV_EXIGIR_CUENTA=1`):

| Endpoint | Qué protegía hoy | Con `TV_EXIGIR_CUENTA=1` |
|---|---|---|
| `GET /tv/canales-cdn` | **el catálogo completo** | sesión + acceso activo |
| `GET /tv/cdnlivetv/{canal}/{codigo}` | resolver el stream | sesión + acceso activo |
| `GET /hls-proxy` | cada segmento de video | sesión + acceso activo |
| `GET /live/index.m3u8` | arrancar FFmpeg | sesión + acceso activo |
| `GET /tv-live/match`, `/tv-live/stream` | escaparate ESPN | **sigue público** |

**Por qué la cookie y no un token:** los segmentos `.ts` los pide hls.js y ahí
no se puede inyectar `Authorization`. La cookie es la única credencial que viaja
sola en esas peticiones, y el backend ya la emite en el login (control #6).

**Dos interruptores separados** (tienen requisitos distintos):

| Variable | Default | Qué cierra | Requisito |
|---|---|---|---|
| `TV_EXIGIR_CATALOGO` | **1 (cerrado)** | `/tv/canales-cdn` y `/tv/cdnlivetv/*` | ninguno: son fetch/XHR y el frontend ya manda el Bearer |
| `TV_EXIGIR_STREAM` | 0 (abierto) | `/hls-proxy` y `/live/index.m3u8` | el reproductor debe enviar la cookie (`withCredentials`) |
| `TV_EXIGIR_CUENTA` | — | fuerza los dos | — |

**Por qué el catálogo va cerrado por defecto:** es exactamente la fuga que
mostró la captura (405 canales y 262 online sin iniciar sesión). Cerrarlo no
necesita tocar el reproductor porque el catálogo se pide con `fetch`, y ahí sí
viaja el header `Authorization`.

**Por qué el stream va abierto por defecto:** los segmentos los pide hls.js y
ahí no hay header. Encenderlo antes de arreglar el reproductor deja la TV en
negro. Primero se verifica, después se enciende.

**Verificación en 10 segundos** (contra producción, sin sesión):

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://<tu-backend>/tv/canales-cdn
# 200 = ABIERTO (mal)      401 = cerrado (bien)
```

**Rollback inmediato:** `TV_EXIGIR_CATALOGO=0` en Railway → vuelve al
comportamiento de antes. Sin tocar código.

**Orden para encender el stream (después):**

1. En el reproductor: `new Hls({ xhrSetup: x => { x.withCredentials = true } })`.
2. DevTools → Network → un segmento `.ts` → comprobar que viaja la cookie
   `sb_session`.
3. `TV_HOSTS_PUBLICOS=espncdn.com,akamaized.net` si el partido destacado de
   ESPN debe seguir público.
4. `TV_EXIGIR_STREAM=1` y reiniciar.

**Lista blanca de hosts** (`TV_HOSTS_PERMITIDOS`): hoy `/hls-proxy` descarga
*cualquier* URL pública del mundo. Poniendo los hosts del CDN, el servidor deja
de poder usarse como proxy abierto (saltarse geobloqueos, gastar tu ancho de
banda). Vacío = comportamiento actual.

### 3.4 Infraestructura (no es código)
| Control | Herramienta | Por qué |
|---|---|---|
| WAF + anti-DDoS + rate limit de borde | **Cloudflare** (proxy naranja) | Frena el ataque *antes* de llegar a Railway |
| Secretos con historial y rotación | Railway Variables / Doppler / Vault | Hoy viven en variables de entorno: bien, pero sin rotación |
| Backups + prueba de restauración | Aiven | Un borrado accidental se recupera con backup, no con código |
| 2FA para el admin | TOTP (Authenticator) | Una contraseña filtrada deja de bastar |
| Monitorización y alertas | Sentry (ya integrado) + alerta sobre `security_events` | Enterarte en minutos, no en semanas |
| Escaneo de dependencias | `pip-audit` en CI | La mayoría de brechas reales son librerías viejas |
| Mínimo privilegio en BD | usuario MySQL sin `DROP`/`ALTER` en runtime | Si hay inyección, limitar el daño |

---

## 4. Verificación rápida (lo que puedes correr tú)

```bash
# 1. Tests de seguridad
python -m unittest test_seguridad

# 2. ¿Se puede falsificar la IP?  (debe devolver SIEMPRE la misma IP)
curl -s -H "X-Forwarded-For: 1.1.1.1" https://<backend>/health

# 3. ¿/health sigue contando si hay admin?  (no debe)
curl -s https://<backend>/health

# 4. ¿El login marca la cookie endurecida?
curl -i -X POST https://<backend>/auth/signin \
  -H "Content-Type: application/json" \
  -d '{"username":"...","password":"..."}' | grep -i set-cookie
#   esperado: HttpOnly; Secure; SameSite=...
#   prohibido: el token en la URL

# 5. Auditoría de los últimos eventos
python -c "import db;[print(e) for e in db.eventos_seguridad(20)]"
```

---

## 5. Runbook de incidente (30 minutos)

1. **Contener**: rotar `MYSQL_PASSWORD` y `CHAT_CONV_SECRET`, `delete from sessions`.
2. **Preservar evidencia**: `select * from security_events order by id desc limit 500;`
   y exportar los logs de Railway **antes** de reiniciar.
3. **Saber el alcance**: ¿qué `usuario`/`ip` hizo `login_ok` que no reconoce?
   ¿hay `admin_denegado` seguidos? ¿pagos nuevos en Pagadito?
4. **Cerrar**: cambiar contraseñas, revisar `redeem_keys` creadas y
   `credit_transactions` recientes.
5. **Aprender**: escribir qué falló y qué control lo habría evitado.

Sin el paso 2, los otros cuatro se hacen a ciegas. Por eso existe
`security_events`.

