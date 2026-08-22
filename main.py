import os
import re
from datetime import timedelta
from urllib.parse import urljoin, quote
import requests as http_requests
from fastapi import FastAPI, Request, Header, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel

import db
import sports
from engine.search_engine import SearchEngine


try:
    from ddgs import DDGS
except ImportError:
    DDGS = None


# ==============================
# CONFIGURACION PARA HOSTING
# ==============================
# Este backend solo soporta You.com.
# No inicializa OpenAI ni Groq en el arranque.

YOU_API_KEY = os.getenv("YOU_API_KEY") or os.getenv("YOU_SEARCH_API_KEY") or ""
YOU_BASE_URL = os.getenv("YOU_BASE_URL", "https://api.you.com/v1/research")

app = FastAPI(
    title="3SIXTYBETS AI WORKSPOT",
    description="IA deportiva con busqueda web automatica.",
    version="2.0"
)

# CORS: permitir el frontend
frontend_origins = os.getenv(
    "FRONTEND_ORIGINS",
    "http://localhost:5173,https://threesixtybets-chat.vercel.app"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in frontend_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Chat(BaseModel):
    mensaje: str
    buscar: bool = True


def limpiar_consulta(mensaje: str) -> str:
    texto = mensaje.strip()
    texto_lower = texto.lower()

    frases = [
        "analiza el partido",
        "analiza partido",
        "analiza",
        "partido",
        "juego",
        "match",
        "edge",
        "apuesta",
        "pronostico",
        "pronóstico"
    ]

    for frase in frases:
        if texto_lower.startswith(frase):
            texto = texto[len(frase):].strip()
            texto_lower = texto.lower()

    return texto if texto else mensaje


# Limite de seguridad para no exceder el TPM (tokens por minuto) del modelo.
# Groq free/on_demand tiers suelen tener limites bajos (p.ej. 8000 TPM), asi
# que recortamos el contexto web ANTES de enviarlo, en vez de dejar que la
# API rechace la peticion por "Request too large".
MAX_CHARS_CONTEXTO_WEB = int(os.getenv("MAX_CHARS_CONTEXTO_WEB", "3500"))
MAX_CHARS_POR_RESULTADO = int(os.getenv("MAX_CHARS_POR_RESULTADO", "220"))


def recortar(texto: str, limite: int) -> str:
    texto = texto or ""
    if len(texto) <= limite:
        return texto
    return texto[:limite].rstrip() + "..."


def buscar_web(mensaje: str, max_resultados: int = 2) -> str:
    partido = limpiar_consulta(mensaje)
    search_engine = SearchEngine()

    # Menos consultas y menos resultados por consulta = menos tokens.
    consultas = [
        f"{partido} odds betting lines",
        f"{partido} recent form stats",
        f"{partido} h2h injuries lineup"
    ]

    resultados = []

    try:
        for consulta in consultas:
            resultados.append(f"\n=== BUSQUEDA WEB: {consulta} ===\n")
            
            # Usar You.com para búsqueda
            datos_busqueda = search_engine.buscar_you(consulta, cantidad=max_resultados)
            
            if not datos_busqueda:
                # Fallback a DDGS si You.com falla
                datos_busqueda = search_engine.buscar_ddgs(consulta, cantidad=max_resultados)

            for r in datos_busqueda:
                titulo = recortar(r.get("title", "Sin titulo"), 100)
                url = r.get("url", "Sin URL")
                contenido = recortar(r.get("body", "Sin contenido"), MAX_CHARS_POR_RESULTADO)

                resultados.append(
                    f"""Titulo: {titulo}
URL: {url}
Contenido: {contenido}
"""
                )

    except Exception as e:
        return f"No se pudo buscar en web. Error: {e}"

    if not resultados:
        return "No se encontraron resultados web."

    contexto_final = "\n".join(resultados)

    # Tope duro global: pase lo que pase, nunca mandamos mas de esto.
    if len(contexto_final) > MAX_CHARS_CONTEXTO_WEB:
        contexto_final = contexto_final[:MAX_CHARS_CONTEXTO_WEB].rstrip() + "\n...(contexto recortado por limite de tokens)"

    return contexto_final


@app.get("/", response_class=PlainTextResponse)
def inicio():
    return "Test"


@app.post("/chat", response_class=PlainTextResponse)
def chat(data: Chat):
    if not YOU_API_KEY:
        return (
            "ERROR: Falta YOU_API_KEY o YOU_SEARCH_API_KEY.\n"
            "En Render agrega la clave de You.com y la variable YOU_BASE_URL."
        )

    reglas = """
Eres 3SIXTYBETS AI WORKSPOT - Analista cuantitativo de apuestas deportivas.

═══════════════════════════════════════════════════════════════════════════════
🎯 METODOLOGIA DE RAZONAMIENTO
═══════════════════════════════════════════════════════════════════════════════

PASO 1: EVIDENCIA DISPONIBLE
- ¿Qué datos confirma la web? (cuotas, estadísticas, lesiones)
- ¿Qué NO aparece? (alineaciones, xG, modelos probabilísticos)
- ¿Qué es amistoso vs oficial? (fiabilidad del mercado)

PASO 2: ANÁLISIS CUANTITATIVO
- Forma reciente: últimos 5 partidos (goles anotados/recibidos)
- Ritmo ofensivo/defensivo: promedio goles por partido
- Tendencia: ¿va en alza o baja?
- Contexto: lesiones, rotaciones, importancia del partido

PASO 3: EVALUACIÓN DE MERCADO
- Cuota implícita = probabilidad según el mercado
- ¿Es realista la cuota vs el rendimiento real?
- Balance: ¿vale la pena el riesgo vs la ganancia?

PASO 4: DECISIÓN CON CONFIANZA
- Si confianza >= 65%: pick recomendado
- Si confianza 50-65%: doble revisar antes
- Si confianza < 50%: no apostar

═══════════════════════════════════════════════════════════════════════════════
📊 TIPOS DE PICKS - FÚTBOL
═══════════════════════════════════════════════════════════════════════════════

VARÍA ENTRE ESTOS (NO SIEMPRE LO MISMO):
- Moneyline (1X2): ganador, doble oportunidad (1X, X2, 12)
- Goles: Over 1.5, 2.5 | Goles por equipo | BTTS (ambos marcan)
- Corners: Total 7.5+ | Por equipo 3.5+ | Ambos equipos 2+ cada uno
- Handicap: Europa (-1, -2) o Asiático (-1.5, -2.5)
- Props: Tiros a puerta, faltas, tarjetas, fueras de juego
- Mitades: Gana primera mitad, segunda mitad, cualquier mitad
- Especiales: Multigoles, gana cualquier mitad

REGLA DE ORO:
- Si equipo fuerte vs débil → handicap o over goles equipo fuerte
- Si partido cerrado → doble oportunidad o over 1.5
- Si dudas → corners (menos variables)

═══════════════════════════════════════════════════════════════════════════════
🧮 EQUILIBRIO PROBABILIDAD + CUOTA
═══════════════════════════════════════════════════════════════════════════════

70% PICKS = Alta probabilidad + cuota decente (1.30-1.60)
30% PICKS = Media probabilidad + cuota mejor (1.60-2.50)

NUNCA: cuota 1.15 en over 4.5 goles (muy arriesgado)
NUNCA: cuota 1.08 en under alto (ROI negativo)
SÍ: cuota 1.25 en over 2.5 goles (probabilidad + valor)
SÍ: cuota 1.50 en under 210.5 NBA (riesgo compensado)

═══════════════════════════════════════════════════════════════════════════════
⚠️ DATOS NO CONFIRMADOS
═══════════════════════════════════════════════════════════════════════════════

Cuando algo NO aparece en las fuentes web:
❌ NO inventar: cuotas, alineaciones definitivas, xG, probabilidades exactas
❌ NO asumir: que las bajas confirmadas afectan (hay suplentes)
✓ SÍ reconocer: "Alineaciones definitivas no confirmadas" → reduce confianza
✓ SÍ usar: datos que SÍ aparecen en web (forma, goles, cuotas visibles)

═══════════════════════════════════════════════════════════════════════════════
📋 FORMATO OBLIGATORIO
═══════════════════════════════════════════════════════════════════════════════

🧠 RAZONAMIENTO:
[Paso 1 - 2: análisis cuantitativo del partido]

💡 EDGE DETECTADO:
[Ventaja estadística encontrada]

🎯 PICK RECOMENDADO:
[Mercado + lógica]
Cuota: [X.XX] | Probabilidad implícita: [XX%] | Confianza: [XX%]

❓ CONSIDERACIONES:
[Riesgos, datos faltantes, condiciones]

VEREDICTO:
[Recomendar apuesta / Doble revisar / No apostar]

═══════════════════════════════════════════════════════════════════════════════
🚨 REGLA CLAVE
═══════════════════════════════════════════════════════════════════════════════

PIENSA COMO MATEMÁTICO, NO COMO HINCHA.
Cuota baja = todos lo ven → poco valor.
Cuota buena + tendencia clara = EDGE.
Dudas = reduce confianza, pero no descartes si hay evidencia.
"""

    # Si se solicita búsqueda web, obtener contexto
    contexto_web = ""
    if data.buscar:
        contexto_web = buscar_web(data.mensaje)
        # Agregar contexto de búsqueda al prompt del sistema
        reglas = reglas + f"\n\nCONTEXTO DE BUSQUEDA WEB OBTENIDO:\n{contexto_web}"

    return SearchEngine().ask_you(data.mensaje, system_prompt=reglas)


# ==============================
# AUTH + DB ENDPOINTS
# ==============================
# Conecta MySQL (db.py) al backend: registro, login, keys, mensajes, health.

@app.on_event("startup")
def _init_database():
    """Inicializa la BD, crea admin y una key por defecto si hace falta.

    Reintenta 3 veces con delay (en producción el DB puede tardar en estar ready).
    Si tras 3 intentos no hay DB, la app arranca de todos modos y los endpoints
    fallarán con 500 al hacer query.
    """
    import time

    for attempt in range(1, 4):
        try:
            db.init_db()  # crea tablas + admin user (ensure_admin_user internamente)

            # Verificar conectividad real con un ping
            ok = db.run_query("SELECT 1 AS ok", fetchone=True)
            if not ok:
                raise Exception("DB query returned None")

            # Crear una key por defecto si no hay ninguna disponible
            existing = db.run_query(
                "SELECT COUNT(*) AS cnt FROM redeem_keys "
                "WHERE claimed_by IS NULL "
                "AND (key_expires_at IS NULL OR key_expires_at > NOW())"
            )
            count = existing[0]["cnt"] if existing else 0

            if count == 0:
                key = db.create_redeem_key(duration_days=30)
                if key:
                    print(f"[startup] Created default redeem key: {key['code']}", flush=True)
                else:
                    print("[startup] Failed to create default key", flush=True)
            else:
                print(f"[startup] DB ready, {count} available key(s)", flush=True)

            print("[startup] Database initialized successfully", flush=True)
            break

        except Exception as exc:
            print(f"[startup] DB init attempt {attempt}/3 failed: {exc}", flush=True)
            if attempt < 3:
                time.sleep(5)
            else:
                print("[startup] DB not available — app continues without DB", flush=True)


# ---- Pydantic models ----
class AuthSignup(BaseModel):
    username: str
    password: str
    redeem_code: str


class AuthSignin(BaseModel):
    username: str
    password: str


class MessageIn(BaseModel):
    role: str
    text: str


class RedeemIn(BaseModel):
    redeem_code: str


class AdminKeyIn(BaseModel):
    duration_days: int
    quantity: int = 1
    expires_in_days: int | None = None


# ---- Auth dependency ----
def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Necesitas iniciar sesion.")
    token = authorization[7:].strip()
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(401, "Sesion expirada. Vuelve a iniciar sesion.")
    return user


def get_admin(user=Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Acceso restringido a administradores.")
    return user


# ---- Endpoints ----

@app.get("/health")
def health():
    """Estado del backend y la base de datos."""
    return db.health_status()


@app.post("/auth/signup")
def auth_signup(data: AuthSignup):
    try:
        user = db.create_user(data.username, data.password, data.redeem_code)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    token = db.create_session(user["id"])
    return {"access_token": token, "user": user}


@app.post("/auth/signin")
def auth_signin(data: AuthSignin):
    user = db.get_user_by_username(data.username)
    if not user or not db.verify_password(data.password, user["password_hash"]):
        raise HTTPException(401, "Usuario o contrasena incorrecta.")
    token = db.create_session(user["id"])
    return {"access_token": token, "user": db.public_user(user)}


@app.post("/auth/signout")
def auth_signout(request: Request, user=Depends(get_current_user)):
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        db.delete_session(token)
    return {"ok": True}


@app.get("/auth/me")
def auth_me(user=Depends(get_current_user)):
    return db.public_user(user)


@app.get("/messages")
def get_messages(user=Depends(get_current_user)):
    messages = db.list_messages(user["id"])
    return {"messages": messages}


@app.post("/messages")
def post_message(data: MessageIn, user=Depends(get_current_user)):
    msg = db.create_message(user["id"], data.role, data.text)
    return msg


@app.post("/redeem")
def redeem_key(data: RedeemIn, user=Depends(get_current_user)):
    try:
        result = db.redeem_key_for_user(user["id"], data.redeem_code)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return result


# ==============================
# ESTADISTICAS DEPORTIVAS (ESPN)
# ==============================

@app.get("/stats/summary")
def stats_summary(user=Depends(get_current_user)):
    """Resumen de todos los deportes: cuantos partidos hay y cuantos en vivo."""
    return sports.get_all_sports_summary()


@app.get("/stats/leagues")
def stats_leagues(user=Depends(get_current_user)):
    """Lista de ligas disponibles para el selector."""
    return sports.get_available_leagues()


@app.get("/stats/live")
def stats_live(sport: str = "soccer", league: str = None, user=Depends(get_current_user)):
    """Partidos de un deporte: en vivo, proximos y finalizados.

    Deportes: soccer, nba, mlb, nfl, tennis
    Para soccer se puede filtrar por liga con ?league=esp.1
    """
    try:
        return sports.get_sport_games(sport, league=league)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/stats/game")
def stats_game(sport: str, event_id: str, user=Depends(get_current_user)):
    """Detalle de un partido: marcador, linescores, estadisticas, H2H, jugadores."""
    try:
        return sports.get_game_detail(sport, event_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Error obteniendo detalle del partido: {exc}")


@app.get("/stats/ai-analysis")
def stats_ai_analysis(sport: str, event_id: str, user=Depends(get_current_user)):
    """Analisis de IA del partido: tendencias, jugador destacado y prediccion."""
    try:
        detail = sports.get_game_detail(sport, event_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Error obteniendo detalle: {exc}")

    # Construir contexto para la IA
    teams = detail.get("teams", [])
    away = next((t for t in teams if t.get("homeAway") != "home"), teams[0] if teams else {})
    home = next((t for t in teams if t.get("homeAway") == "home"), teams[1] if len(teams) > 1 else {})

    context_parts = [
        f"Deporte: {detail.get('label', sport)}",
        f"Estado: {detail.get('status', 'N/A')}",
        f"Partido: {away.get('name', '?')} ({away.get('score', '-')}) vs {home.get('name', '?')} ({home.get('score', '-')})",
    ]

    # Records
    if away.get("records"):
        context_parts.append(f"Racha {away.get('name')}: {away['records'][0]}")
    if home.get("records"):
        context_parts.append(f"Racha {home.get('name')}: {home['records'][0]}")

    # H2H
    h2h = detail.get("head_to_head", [])
    if h2h:
        context_parts.append("\nHistorial H2H (ultimos enfrentamientos):")
        for h in h2h[:5]:
            ha = h.get("away", {})
            hh = h.get("home", {})
            context_parts.append(
                f"  {ha.get('name','?')} {ha.get('score','-')} - {hh.get('score','-')} {hh.get('name','?')} ({h.get('status','')})"
            )

    # Ultimos partidos
    for t in [away, home]:
        recent = t.get("recent_games", [])
        if recent:
            context_parts.append(f"\nUltimos partidos de {t.get('name','?')}:")
            for r in recent[:3]:
                ra = r.get("away", {})
                rh = r.get("home", {})
                context_parts.append(
                    f"  {ra.get('name','?')} {ra.get('score','-')} - {rh.get('score','-')} {rh.get('name','?')} ({r.get('status','')})"
                )

    # Jugadores destacados
    key_players = detail.get("key_players", [])
    if key_players:
        context_parts.append("\nJugadores destacados:")
        for p in key_players[:4]:
            stats_str = ", ".join(f"{s['name']}: {s['value']}" for s in p.get("stats", [])[:3])
            context_parts.append(f"  {p.get('name','?')} - {stats_str}")

    # Estadisticas del equipo
    for t in [away, home]:
        stats = t.get("statistics", [])
        if stats:
            context_parts.append(f"\nEstadisticas {t.get('name','?')}:")
            for s in stats[:8]:
                context_parts.append(f"  {s['name']}: {s['label']}")

    contexto = "\n".join(context_parts)

    prompt = f"""Eres 3SIXTYBETS AI - analista deportivo. Analiza este partido y da:
1. Tendencia del partido (quien domina, momento del juego)
2. Jugador destacado (si hay datos) y por que
3. Pronostico/edge si aplica

DATOS DEL PARTIDO:
{contexto}

Responde en espanol, conciso (max 200 palabras), formato:
🧠 Analisis IA:
[jugada a jugada]
⭐ Jugador destacado:
[nombre y razon]
🎯 Tendencia:
[pronostico]"""

    try:
        from engine.search_engine import SearchEngine
        respuesta = SearchEngine().ask_you(prompt)
        # Limpiar asteriscos de formato markdown
        if respuesta:
            respuesta = respuesta.replace("*", "").replace("#", "")
        return {"analysis": respuesta, "context": contexto}
    except Exception as exc:
        return {"analysis": f"No se pudo generar analisis: {exc}", "context": contexto}


@app.get("/admin/keys")
def admin_list_keys(user=Depends(get_admin)):
    keys = db.list_redeem_keys()
    return {"keys": keys}


@app.post("/admin/keys")
def admin_create_keys(data: AdminKeyIn, user=Depends(get_admin)):
    key_expires_at = None
    if data.expires_in_days:
        key_expires_at = db.now_utc() + timedelta(days=data.expires_in_days)
    created = []
    for _ in range(max(1, data.quantity)):
        key = db.create_redeem_key(
            duration_days=data.duration_days,
            created_by=user["id"],
            key_expires_at=key_expires_at,
        )
        created.append({
            "code": key["code"],
            "duration_days": key["duration_days"],
            "status": "available",
            "expires_at": key["key_expires_at"].isoformat() if key.get("key_expires_at") else None,
        })
    return {"keys": created}


# ==============================
# PROXY HLS
# ==============================
# En desarrollo Vite lo sirve con el plugin /vite-plugins/hls-proxy.js.
# En producción (build estático) el browser no puede pedir streams
# http:// desde https:// (mixed content) ni pasar el CORS de los servidores
# IPTV.  Este endpoint las resuelve: el browser habla con nosotros por https
# (mismo origen del API) y Python re-encamina el stream al servidor real.

HLS_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Cabeceras que no deben retransmitirse al cliente
_HOP = {
    "connection", "keep-alive", "transfer-encoding", "upgrade",
    "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "content-encoding", "content-length",
}


def _proxy_base(request: Request) -> str:
    """URL canónica del propio endpoint /hls-proxy (para reescritura de manifests)."""
    scheme = request.headers.get("x-forwarded-proto", "https")
    host = request.headers.get("host", request.url.netloc)
    return f"{scheme}://{host}/hls-proxy"


def _wrap(abs_url: str, proxy_base: str, referer: str | None = None) -> str:
    """Construye una URL *hacia nuestro proxy* a partir de una URL absoluta."""
    out = f"{proxy_base}?url={quote(abs_url, safe='')}"
    if referer:
        out += f"&referer={quote(referer, safe='')}"
    return out


def _is_manifest(path: str, content_type: str = "") -> bool:
    low = path.lower()
    return (
        low.endswith(".m3u8")
        or low.endswith(".m3u")
        or "mpegurl" in content_type
        or "x-mpegurl" in content_type
        or "vnd.apple.mpegurl" in content_type
    )


def _rewrite_manifest(text: str, base_url: str, proxy_base: str, referer: str | None) -> str:
    """
    Reescribe todas las URLs del m3u8 para que pasen por nuestro proxy.
    No toca etiquetas que no contengan URI.
    """
    out_lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            out_lines.append(line)
            continue

        if stripped.startswith("#"):
            # #EXT-X-KEY:URI="..."  #EXT-X-MAP:URI="..." etc.
            def _repl(m):
                uri = m.group(1)
                if not uri:
                    return m.group(0)
                try:
                    abs_url = urljoin(base_url, uri)
                    return f'URI="{_wrap(abs_url, proxy_base, referer)}"'
                except Exception:
                    return m.group(0)

            line = re.sub(r'URI="([^"]+)"', _repl, line)
            out_lines.append(line)
        else:
            try:
                abs_url = urljoin(base_url, stripped)
                out_lines.append(_wrap(abs_url, proxy_base, referer))
            except Exception:
                out_lines.append(line)

    return "\n".join(out_lines)


@app.get("/hls-proxy")
def hls_proxy(request: Request, url: str, referer: str = None):
    """Proxy transparente para streams HLS (.m3u8, .ts, .key, .aac)."""
    if not url or not re.match(r"^https?://", url.strip()):
        from fastapi import HTTPException
        raise HTTPException(400, "Parámetro ?url= inválido o ausente")

    target = url.strip()

    headers = {"User-Agent": HLS_USER_AGENT}
    if referer:
        headers["Referer"] = referer
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]

    try:
        resp = http_requests.get(
            target, headers=headers, stream=True, timeout=(5, 30)
        )
    except http_requests.RequestException as exc:
        from fastapi import HTTPException
        raise HTTPException(502, f"Error contacting upstream: {exc}")

    content_type = resp.headers.get("content-type", "")
    final_url = resp.url  # refleja redirects -> base correcta para rewrite
    proxy_base = _proxy_base(request)

    # --- Manifest reescrito ---
    if _is_manifest(final_url, content_type) and resp.status_code == 200:
        text = resp.text
        rewritten = _rewrite_manifest(text, final_url, proxy_base, referer)
        return PlainTextResponse(
            rewritten,
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-store"},
        )

    # --- Segmentos / llaves: passthrough en streaming ---
    # Forward del código de estado
    if not resp.ok:
        body = resp.text[:500] if resp.text else ""
        return PlainTextResponse(
            f"Upstream {resp.status_code}: {body}",
            status_code=resp.status_code,
            media_type="text/plain",
            headers={"Cache-Control": "no-store"},
        )

    out_headers = {}
    for key, val in resp.headers.items():
        if key.lower() not in _HOP:
            out_headers[key] = val
    if not content_type:
        out_headers["Content-Type"] = "video/mp2t"

    def _stream():
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                yield chunk

    return StreamingResponse(
        _stream(),
        media_type=out_headers.get("Content-Type", "application/octet-stream"),
        headers=out_headers,
    )


