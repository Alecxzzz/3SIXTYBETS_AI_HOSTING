"""
Extras de 3SIXTYBETS: Pick del Dia (IA), Simulador de Parlay,
Track Record y paginas de Perfil/Parlay/Track.

Los endpoints los registra main.py importando desde aqui.
"""

import json
import os
import re
import threading
import time

# ------------------------------------------------------------------
# PICK DEL DIA (la IA elige el mejor pick del dia y lo justifica)
# ------------------------------------------------------------------

_pick_del_dia_cache = {"key": None, "data": None}


def _picks_candidatos_hoy() -> list:
    """Picks de hoy elegibles: pendientes, vigentes, calidad y cuota > 1.20."""
    import dashboard
    import db

    picks = db.list_picks_hoy() or []
    return [
        p for p in picks
        if p.get("result") == "PENDIENTE"
        and dashboard._pick_calidad_ok(p)
        and dashboard._evento_vigente(p)
        and (p.get("odds") or 0) > 1.20
    ]


def _conf_num(p: dict) -> float:
    """Confianza como numero (llega como '68%' o 68)."""
    v = p.get("confidence")
    if v is None:
        return 0.0
    if isinstance(v, str):
        v = re.sub(r"[^0-9.]", "", v) or 0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _ia_elegir(candidatos: list, cache_key: str):
    """Eleccion de la IA en segundo plano; actualiza la cache al terminar."""
    try:
        import dashboard

        resumen = [
            {
                "id": p["id"],
                "partido": p.get("eventName"),
                "liga": p.get("league") or p.get("sportLabel"),
                "apuesta": f"{p.get('titulo')} -> {p.get('selection')}",
                "cuota": p.get("odds"),
                "confianza": p.get("confidence"),
                "razon": (p.get("rationale") or "")[:200],
            }
            for p in candidatos
        ]
        prompt = (
            "Eres el analista jefe de 3SIXTYBETS. De estos picks generados hoy:\n"
            f"{json.dumps(resumen, ensure_ascii=False, indent=1)}\n\n"
            "Elige UN SOLO pick como 'PICK DEL DIA' (el que mejor combina valor, "
            "probabilidad y confiabilidad). Responde SOLO JSON:\n"
            '{"pickId": "...", "justificacion": "2-3 frases concretas explicando '
            'por que es el mejor, mencionando la razon estadistica principal"}'
        )
        texto, _modelo = dashboard._preguntar_ia(prompt)
        if not texto:
            return
        m = re.search(r"\{.*\}", texto, re.DOTALL)
        if not m:
            return
        eleccion = json.loads(m.group(0))
        match = next((p for p in candidatos if p["id"] == eleccion.get("pickId")), None)
        if not match:
            return
        _pick_del_dia_cache.update(
            key=cache_key,
            data={
                "pick": match,
                "justificacion": (eleccion.get("justificacion") or "").strip(),
                "elegido_por": "ia",
            },
        )
    except Exception as exc:
        print(f"[Extras] pick del dia IA error: {exc}")


def pick_del_dia(force: bool = False) -> dict:
    """El pick estrella del dia. Responde INSTANTANEO con el pick de mayor
    confianza y lanza la eleccion de la IA en un hilo de fondo: el proximo
    fetch (minuto siguiente) ya trae la justificacion de la IA.
    """
    ahora_hora = time.strftime("%Y%m%d%H")
    candidatos = _picks_candidatos_hoy()
    if not candidatos:
        data = {"pick": None, "justificacion": "", "elegido_por": ""}
        _pick_del_dia_cache.update(key=ahora_hora, data=data)
        return data

    # Orden estable por confianza (fallback determinista)
    candidatos.sort(key=lambda p: (-_conf_num(p), -(p.get("odds") or 0)))
    cache_key = f"{ahora_hora}:{candidatos[0]['id']}"
    if not force and _pick_del_dia_cache["key"] == cache_key:
        return _pick_del_dia_cache["data"]

    # Respuesta inmediata: pick de mayor confianza con su rationale real
    elegido = candidatos[0]
    data = {
        "pick": elegido,
        "justificacion": (elegido.get("rationale") or "").strip(),
        "elegido_por": "confianza",
    }
    _pick_del_dia_cache.update(key=cache_key, data=data)

    # La IA elige en segundo plano (no bloquea la respuesta)
    threading.Thread(target=_ia_elegir, args=(candidatos[:12], cache_key), daemon=True).start()
    return data


# ------------------------------------------------------------------
# SIMULADOR DE PARLAY
# ------------------------------------------------------------------

def simular_parlay(ids: list, stake: float = 10.0) -> dict:
    """Cuota combinada, probabilidad implicita y pago potencial del parlay.

    Los picks deben ser de HOY y pendientes, con cuota > 1.20.
    """
    import dashboard
    import db

    picks = db.list_picks_por_ids(ids)
    picks = [
        p for p in picks
        if p.get("result") == "PENDIENTE"
        and (p.get("odds") or 0) > 1.20
        and dashboard._pick_calidad_ok(p)
    ]
    picks.sort(key=lambda p: ids.index(p["id"]))
    if len(picks) < 2:
        return {"ok": False, "error": "Selecciona al menos 2 picks validos del dia."}
    if len(picks) > 8:
        return {"ok": False, "error": "Maximo 8 picks por parlay."}

    cuota_total = 1.0
    prob_implicita = 1.0
    items = []
    for p in picks:
        cuota = float(p["odds"])
        cuota_total *= cuota
        prob_implicita *= 1.0 / cuota
        items.append({
            "id": p["id"],
            "partido": p.get("eventName"),
            "apuesta": f"{p.get('titulo')} -> {p.get('selection')}",
            "cuota": cuota,
            "confianza": p.get("confidence"),
        })

    return {
        "ok": True,
        "items": items,
        "picks": len(items),
        "stake": round(stake, 2),
        "cuotaTotal": round(cuota_total, 2),
        "pagoPotencial": round(stake * cuota_total, 2),
        "ganancia": round(stake * cuota_total - stake, 2),
        "probImplicita": round(100 * prob_implicita, 1),
        "probEstimada": round(100 * prob_implicita * 0.72, 1),  # ajustada al historico
    }


def opinion_ia_parlay(data: dict) -> str:
    """Veredicto corto de la IA sobre el parlay armado (opcional)."""
    import dashboard

    prompt = (
        "Eres el analista de 3SIXTYBETS. Evalua este parlay:\n"
        f"{json.dumps(data.get('items'), ensure_ascii=False)}\n"
        f"Cuota total: {data.get('cuotaTotal')} | Prob implicita: {data.get('probImplicita')}%\n"
        "En maximo 3 frases: ¿vale la pena? Considera que la IA lleva ~72% "
        "historico por pick individual y que en parlay las probabilidades se "
        "multiplican. Responde en espanol, directo, sin listas."
    )
    texto, _ = dashboard._preguntar_ia(prompt)
    return (texto or "").strip()[:400]


# ------------------------------------------------------------------
# SOPORTE CON IA (usa datos reales de la BD del usuario)
# ------------------------------------------------------------------

WHATSAPP = "50588287489"
KB_FILE = os.path.join(os.path.dirname(__file__), "soporte_kb.json")
KB_DEFAULT = {
    "planes": {
        "plan15": "suscripcion de 15 dias ($5)",
        "plan30": "suscripcion de 30 dias ($10)",
    },
    "faq": [
        "P: ¿Como activo mi suscripcion? R: Pagas con Pagadito desde el boton de planes; al confirmarse el pago (estado COMPLETED) los dias se activan automaticamente.",
        "P: Pague y no se activaron mis dias R: Los pagos tardan 1-3 minutos en confirmarse; si tras 5 minutos no se activaron, es un caso para WhatsApp.",
        "P: ¿Cada cuanto se generan los picks? R: La IA genera picks del dia automaticamente cada ciclo del scheduler y los resuelve con el marcador real al terminar cada partido.",
        "P: ¿Por que no veo todos los picks? R: Los picks con cuota menor a 1.20 o datos incompletos se descartan automaticamente por calidad.",
        "P: ¿Puedo ver el historial de aciertos? R: Si, en el dashboard (pestaña Acertados) y el track record completo en /track.",
        "P: ¿Los picks garantizan ganar? R: No. Es analisis estadistico con ~67% de efectividad historica; apuesta con responsabilidad.",
    ],
    "reglas": "Nunca prometas resultados de apuestas. Nunca des picks de apuestas en el soporte (para eso esta la IA principal).",
}


def _kb() -> dict:
    """Base de conocimiento de soporte (editable sin tocar codigo)."""
    try:
        with open(KB_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else KB_DEFAULT
    except (OSError, ValueError):
        return KB_DEFAULT


def _contexto_usuario(user: dict) -> str:
    """Datos reales del usuario para que la IA responda con hechos."""
    import db
    import json as _json

    pu = db.public_user(user)
    exp = pu.get("access_expires_at")
    if exp:
        from datetime import datetime

        try:
            dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
            ilimitado = dt.year >= 9999
            dias = max((dt - datetime.utcnow().replace(tzinfo=dt.tzinfo)).days, 0)
        except (ValueError, TypeError):
            ilimitado, dias = False, 0
    else:
        ilimitado, dias = False, 0

    ordenes = db.list_pagadito_orders_for_user(user["id"]) or []
    pagos = [
        {
            "plan": o.get("plan_code"),
            "monto": float(o["amount"]) if o.get("amount") is not None else None,
            "estado": o.get("status"),
            "fecha": o["created_at"].isoformat()[:10] if o.get("created_at") else None,
        }
        for o in ordenes[:5]
    ]
    hist = db.count_aciertos_historico()

    return _json.dumps(
        {
            "usuario": pu.get("username"),
            "rol": pu.get("role"),
            "plan": "ILIMITADO" if ilimitado else f"{dias} dias restantes",
            "expira": None if ilimitado else str(exp)[:10] if exp else None,
            "efectividad_historica_ia": f"{hist['aciertos']}/{hist['resueltos']} ({round(100 * hist['aciertos'] / hist['resueltos']) if hist['resueltos'] else 0}%)",
            "ultimos_pagos": pagos,
        },
        ensure_ascii=False,
    )


def soporte_chat(user: dict, mensaje: str) -> dict:
    """Respuesta de la IA de soporte exclusiva: prompt dedicado + base de
    conocimiento propia + datos reales del usuario. Cada conversacion se
    guarda en la BD (dataset para futuras mejoras/fine-tuning).
    """
    import dashboard
    import db

    mensaje = (mensaje or "").strip()[:600]
    if not mensaje:
        return {"respuesta": "Cuentame en que te ayudo :)"}

    contexto = _contexto_usuario(user)
    kb = _kb()
    prompt = (
        "Eres el asistente EXCLUSIVO de soporte de 3SIXTYBETS (plataforma de "
        "pronosticos deportivos con IA, canales de TV en vivo y suscripciones "
        "pagadas con Pagadito). Escribe MUY amable, con emojis discretos, en "
        "espanol, maximo 4 frases, sin listas.\n\n"
        "BASE DE CONOCIMIENTO OFICIAL (respeta esto, no inventes):\n"
        f"Planes: {json.dumps(kb.get('planes', {}), ensure_ascii=False)}\n"
        "FAQ:\n- " + "\n- ".join(kb.get("faq", [])) + "\n"
        f"Reglas: {kb.get('reglas', '')}\n\n"
        "DATOS REALES del usuario (usalos, no los inventes, no los muestres "
        "en crudo a menos que sirvan):\n"
        f"{contexto}\n\n"
        "REGLA IMPORTANTE: si el problema es grave o tecnico y tu no puedes "
        "resolverlo (pago rechazado, dinero no acreditado, falla del canal de "
        "TV, reclamo), termina recomendando con carino escribir al WhatsApp de "
        f"soporte: 50588287489 (wa.me/{WHATSAPP}).\n\n"
        f"El usuario pregunta: {mensaje}"
    )
    texto, _modelo = dashboard._preguntar_ia(prompt)
    respuesta = (texto or "").strip()
    if not respuesta:
        respuesta = (
            "Ahora mismo estoy teniendo problemas para procesar tu mensaje 😅. "
            f"Escribenos directo al WhatsApp {WHATSAPP} y te ayudamos al instante."
        )
    respuesta = respuesta[:700]

    # Dataset: cada conversacion queda guardada para futuras mejoras
    try:
        db.save_support_chat(user["id"], user.get("username", "?"), mensaje, respuesta)
    except Exception as exc:
        print(f"[Extras] save_support_chat error: {exc}")

    return {"respuesta": respuesta}



# ------------------------------------------------------------------
# PAGINAS HTML (perfil / parlay / track) - tema oscuro del sitio
# ------------------------------------------------------------------

_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0b0d14;color:#e8eaf6;font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh;padding:18px}
.wrap{max-width:860px;margin:0 auto}
h1{font-size:1.35rem;color:#a78bfa;margin-bottom:4px}
.sub{color:#8b93b5;font-size:.85rem;margin-bottom:18px}
.card{background:#141826;border:1px solid #232a42;border-radius:14px;padding:16px;margin-bottom:14px}
.row{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-bottom:1px solid #1d2338;font-size:.92rem}
.row:last-child{border-bottom:none}
.k{color:#8b93b5}.v{font-weight:600;text-align:right}
input{background:#0e1120;border:1px solid #2a3352;color:#e8eaf6;border-radius:10px;padding:10px 12px;width:100%;font-size:.95rem;margin-bottom:10px}
button{background:linear-gradient(135deg,#7c5ce7,#6c5ce7);color:#fff;border:none;border-radius:10px;padding:11px 18px;font-weight:700;font-size:.95rem;cursor:pointer;width:100%}
.err{color:#f87171;font-size:.85rem;margin-bottom:10px;min-height:1.2em}
.pill{display:inline-block;border-radius:999px;padding:2px 10px;font-size:.75rem;font-weight:700}
.pill.hi{background:#14532d;color:#4ade80}
.pill.mid{background:#78350f;color:#fbbf24}
.pill.lo{background:#7f1d1d;color:#f87171}
table{width:100%;border-collapse:collapse;font-size:.88rem}
th{color:#8b93b5;text-align:left;padding:6px 8px;border-bottom:1px solid #232a42;font-weight:600;font-size:.78rem;text-transform:uppercase}
td{padding:7px 8px;border-bottom:1px solid #1d2338}
.bar{position:fixed;bottom:0;left:0;right:0;background:#141826ee;border-top:1px solid #2a3352;padding:12px 18px;display:flex;justify-content:space-between;align-items:center}
.bar b{color:#a78bfa}
label.ck{display:flex;gap:10px;align-items:flex-start;padding:10px 12px;border:1px solid #232a42;border-radius:10px;margin-bottom:8px;cursor:pointer}
label.ck.sel{border-color:#7c5ce7;background:#1b1f36}
label.ck input{margin-top:3px;width:auto}
label.ck .t{flex:1}
label.ck .o{color:#a78bfa;font-weight:700;white-space:nowrap}
"""


def _pagina(titulo: str, cuerpo: str, script: str = "") -> str:
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{titulo} - 3SIXTYBETS</title><style>{_CSS}</style></head>
<body><div class="wrap">{cuerpo}</div>
<script>
const TK_KEY = "sb_token";
function tk() {{ const q = new URLSearchParams(location.search).get("token"); if (q) {{ localStorage.setItem(TK_KEY, q); return q; }} return localStorage.getItem(TK_KEY) || ""; }}
function setTk(t) {{ localStorage.setItem(TK_KEY, t); }}
async function api(path, opts={{}}) {{
  const h = {{"Content-Type": "application/json"}};
  if (tk()) h["Authorization"] = "Bearer " + tk();
  const r = await fetch(path, {{...opts, headers: {{...h, ...(opts.headers||{{}})}}}});
  const d = await r.json().catch(() => ({{}}));
  return {{ok: r.ok, status: r.status, data: d}};
}}
function esc(s) {{ return String(s ?? "").replace(/[&<>"']/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}})[c]); }}
{script}
</script></body></html>"""


def _login_html() -> str:
    return """
<div class="card" id="login">
  <h1>Iniciar sesion</h1>
  <p class="sub">Ingresa para ver tu informacion</p>
  <div class="err" id="err"></div>
  <input id="u" placeholder="Usuario" autocomplete="username">
  <input id="p" type="password" placeholder="Contrasena" autocomplete="current-password">
  <button onclick="hacerLogin()">Entrar</button>
</div>"""


def _login_js() -> str:
    return """
async function hacerLogin() {
  const err = document.getElementById('err');
  err.textContent = '';
  const r = await api('/auth/signin', {method:'POST', body: JSON.stringify({username: document.getElementById('u').value.trim(), password: document.getElementById('p').value})});
  if (!r.ok) { err.textContent = r.data.detail || 'Usuario o contrasena incorrecta.'; return; }
  setTk(r.data.access_token);
  location.reload();
}
"""


# ------------------------- PERFIL -------------------------

def html_perfil() -> str:
    cuerpo = _login_html() + """
<div id="contenido" style="display:none">
  <h1>Mi perfil</h1>
  <p class="sub">Tu cuenta y tu suscripcion</p>
  <div class="card"><div id="cuenta"></div></div>
  <div class="card">
    <h1 style="font-size:1.05rem">Historial de pagos</h1>
    <div id="pagos" style="margin-top:10px"></div>
  </div>
  <button onclick="setTk('');location.reload()">Cerrar sesion</button>
</div>"""
    script = _login_js() + """
function pillEstado(e) {
  const v = String(e||'').toUpperCase();
  const cls = v==='COMPLETED' ? 'hi' : (v==='PROCESANDO'||v==='PENDING' ? 'mid' : 'lo');
  return `<span class="pill ${cls}">${esc(v||'?')}</span>`;
}
async function init() {
  const me = await api('/auth/me');
  if (!me.ok) { document.getElementById('login').style.display='block'; return; }
  document.getElementById('login').style.display='none';
  document.getElementById('contenido').style.display='block';
  const u = me.data;
  const exp = u.access_expires_at ? new Date(u.access_expires_at) : null;
  const anos = exp && exp.getFullYear() >= 9999;
  const restantes = exp ? Math.ceil((exp - new Date())/86400000) : 0;
  document.getElementById('cuenta').innerHTML = `
    <div class="row"><span class="k">Usuario</span><span class="v">${esc(u.username)}</span></div>
    <div class="row"><span class="k">Rol</span><span class="v">${u.role==='admin'?'Administrador':'Usuario'}</span></div>
    <div class="row"><span class="k">Plan</span><span class="v">${anos ? '<span class="pill hi">ILIMITADO</span>' : (restantes>0 ? `<span class="pill mid">${restantes} dias restantes</span>` : '<span class="pill lo">EXPIRADO</span>')}</span></div>
    <div class="row"><span class="k">Acceso vence</span><span class="v">${exp && !anos ? esc(exp.toLocaleDateString('es-NI')) : 'Nunca expira'}</span></div>`;
  const tx = await api('/transactions');
  const lista = tx.data.transactions || [];
  document.getElementById('pagos').innerHTML = lista.length ? `<table><tr><th>Fecha</th><th>Plan</th><th>Monto</th><th>Estado</th></tr>${
    lista.map(t => `<tr><td>${esc((t.fecha||'').slice(0,10))}</td><td>${esc(t.plan||'')}</td><td>${esc(t.monto??'')} ${esc(t.moneda||'')}</td><td>${pillEstado(t.estado)}</td></tr>`).join('')
  }</table>` : '<p class="sub">Sin pagos registrados todavia.</p>';
}
init();
"""
    return _pagina("Mi perfil", cuerpo, script)


# ------------------------- PARLAY -------------------------

def html_parlay() -> str:
    cuerpo = _login_html() + """
<div id="contenido" style="display:none">
  <h1>Simulador de Parlay</h1>
  <p class="sub">Combina 2 a 8 picks del dia. La cuota y la probabilidad se multiplican.</p>
  <div id="lista"></div>
  <div class="card">
    <label class="k">Tu apuesta (stake)</label>
    <input id="stake" type="number" value="10" min="1" step="1" style="margin-top:6px">
    <button onclick="simular()">Simular parlay</button>
    <div class="err" id="perr"></div>
  </div>
</div>
<div class="bar" id="barra" style="display:none">
  <span><span id="n">0</span> picks</span>
  <button style="width:auto" onclick="simular()">Simular</button>
</div>
<div id="resultado"></div>"""
    return _pagina("Simulador de Parlay", cuerpo, _parlay_js())


def _parlay_js() -> str:
    return _login_js() + """
let SELECCION = [];
async function init() {
  const me = await api('/auth/me');
  if (!me.ok) { document.getElementById('login').style.display='block'; return; }
  document.getElementById('login').style.display='none';
  document.getElementById('contenido').style.display='block';
  const r = await api('/dashboard/picks');
  const picks = (r.data.picks||[]).filter(p => (p.odds||0) > 1.2);
  const box = document.getElementById('lista');
  if (!picks.length) { box.innerHTML = '<div class="card"><p class="sub">No hay picks pendientes del dia todavia.</p></div>'; return; }
  box.innerHTML = picks.map(p => `
    <label class="ck" id="ck-${esc(p.id)}">
      <input type="checkbox" onchange="toggle('${esc(p.id)}', this)">
      <span class="t"><b>${esc(p.eventName)}</b><br>
      <span class="sub" style="margin:0">${esc(p.titulo)} - ${esc(p.selection)}</span><br>
      <span class="sub" style="margin:0">confianza ${esc(p.confianza??'?')}</span></span>
      <span class="o">${esc(p.odds)}</span>
    </label>`).join('');
}
function toggle(id, el) {
  const i = SELECCION.indexOf(id);
  if (i >= 0 && !el.checked) { SELECCION.splice(i,1); document.getElementById('ck-'+id).classList.remove('sel'); }
  if (el.checked) { SELECCION.push(id); document.getElementById('ck-'+id).classList.add('sel'); }
  document.getElementById('n').textContent = SELECCION.length;
  document.getElementById('barra').style.display = SELECCION.length ? 'flex' : 'none';
}
async function simular() {
  const err = document.getElementById('perr');
  err.textContent = '';
  if (SELECCION.length < 2) { err.textContent = 'Selecciona al menos 2 picks.'; return; }
  const r = await api('/dashboard/parlay', {method:'POST', body: JSON.stringify({ids: SELECCION, stake: parseFloat(document.getElementById('stake').value)||10})});
  if (!r.ok || !r.data.ok) { err.textContent = r.data.error || 'Error simulando.'; return; }
  const d = r.data;
  document.getElementById('resultado').innerHTML = `
    <div class="card">
      <h1 style="font-size:1.05rem">Resultado (${d.picks} picks)</h1>
      <div style="margin-top:8px">
        <div class="row"><span class="k">Cuota combinada</span><span class="v" style="color:#a78bfa;font-size:1.15rem">${d.cuotaTotal}</span></div>
        <div class="row"><span class="k">Pago potencial</span><span class="v" style="color:#4ade80">$${d.pagoPotencial}</span></div>
        <div class="row"><span class="k">Ganancia</span><span class="v" style="color:#4ade80">$${d.ganancia}</span></div>
        <div class="row"><span class="k">Probabilidad implicita (casino)</span><span class="v">${d.probImplicita}%</span></div>
        <div class="row"><span class="k">Probabilidad estimada (historico IA)</span><span class="v">${d.probEstimada}%</span></div>
      </div>
      <button onclick="pedirOpinion()" style="margin-top:10px">Que dice la IA?</button>
      <div class="sub" id="opinion" style="margin-top:8px"></div>
    </div>`;
  document.getElementById('resultado').scrollIntoView({behavior:'smooth'});
}
async function pedirOpinion() {
  const box = document.getElementById('opinion');
  box.textContent = 'La IA esta analizando el parlay...';
  const r = await api('/dashboard/parlay/opinion', {method:'POST', body: JSON.stringify({ids: SELECCION, stake: parseFloat(document.getElementById('stake').value)||10})});
  box.textContent = r.ok && r.data.opinion ? r.data.opinion : 'La IA no pudo responder ahora, intenta de nuevo.';
}
init();
"""


# ------------------------- TRACK RECORD -------------------------

def html_track() -> str:
    cuerpo = """
<h1>Track Record de la IA</h1>
<p class="sub">Efectividad por familia de mercado, deporte y liga (solo picks resueltos)</p>
<div id="track"><p class="sub">Cargando...</p></div>"""
    script = """
function fila(d) {
  const cls = d.efectividad >= 65 ? 'hi' : (d.efectividad >= 50 ? 'mid' : 'lo');
  return `<div class="row"><span class="k">${esc(d.nombre)}${d.confiable ? '' : ' <span class="sub" style="display:inline">(pocos datos)</span>'}</span><span class="v"><span class="pill ${cls}">${d.efectividad}%</span> ${d.aciertos}/${d.resueltos}</span></div>`;
}
async function init() {
  const r = await api('/dashboard/track-record');
  const d = r.data;
  const box = document.getElementById('track');
  if (!r.ok || !d.mercados) { box.innerHTML = '<div class="card"><p class="sub">Sin datos todavia.</p></div>'; return; }
  const secciones = [
    ['Por familia de mercado', d.mercados],
    ['Por deporte', d.deportes],
    ['Por liga', (d.ligas||[]).slice(0,12)],
  ];
  box.innerHTML = `
    <div class="card">
      <div class="row"><span class="k">EFECTIVIDAD HISTORICA TOTAL</span><span class="v" style="font-size:1.2rem;color:#4ade80">${d.total.efectividad}%</span></div>
      <div class="row"><span class="k">Picks resueltos</span><span class="v">${d.total.aciertos}/${d.total.resueltos}</span></div>
    </div>` +
    secciones.map(([t, items]) => items && items.length ? `
      <div class="card"><h1 style="font-size:1.05rem">${t}</h1>
      <div style="margin-top:6px">${items.map(fila).join('')}</div></div>` : ''
    ).join('');
}
init();
"""
    return _pagina("Track Record", cuerpo, script)



