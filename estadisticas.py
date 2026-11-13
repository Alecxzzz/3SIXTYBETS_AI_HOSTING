# -*- coding: utf-8 -*-
"""PAGINA DE ESTADISTICAS DEL PARTIDO, servida por el backend.

Por que existe
--------------
La pantalla de estadisticas vivia SOLO en el frontend de Vercel, asi que todo lo
que ESPN manda (sede, arbitros, donde verlo, parciales, jugada a jugada,
probabilidad) se quedaba en la API sin que nadie lo pintara. Esta pagina lo
renderiza en el servidor, igual que /perfil, /parlay o /track: se abre con
/estadisticas?event_id=... y no depende del despliegue del frontend.

Que muestra, en el orden de la ficha de ESPN:
  1. Marcador: equipos, records ("9-3, 5-1 Local"), parciales por cuarto.
  2. GAME INFORMATION: fecha, sede con foto, asistencia y donde verlo.
  3. OFFICIATING CREW: los tres arbitros.
  4. Probabilidad de victoria: dos areas apiladas (local abajo, visita arriba).
  5. Jugada a jugada.

Se usa `string.Template` ($variable) en vez de f-strings porque el CSS y el JS
estan llenos de llaves y con f-string habria que escaparlas todas.
"""

import json
from html import escape
from string import Template

_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin:0; background:#0b0f19; color:#e6e8ee;
       font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
.wrap { max-width:980px; margin:0 auto; padding:18px 16px 48px; }
a { color:#a78bfa; }
.card { background:#111827; border:1px solid #1f2937; border-radius:14px;
        padding:16px 18px; margin-bottom:14px; }
h1 { font-size:18px; margin:0 0 4px; }
h2 { font-size:13px; letter-spacing:.04em; text-transform:uppercase;
     color:#9aa4b2; margin:0 0 12px; }
.muted { color:#9aa4b2; }
.marcador { display:grid; grid-template-columns:1fr auto 1fr; align-items:center;
            gap:10px; margin-top:10px; }
.equipo { display:flex; align-items:center; gap:10px; }
.equipo.local { justify-content:flex-end; text-align:right; flex-direction:row-reverse; }
.equipo img { width:46px; height:46px; object-fit:contain; }
.equipo .nom { font-weight:700; font-size:15px; line-height:1.15; }
.equipo .rec { font-size:12px; color:#9aa4b2; }
.equipo .pts { font-size:36px; font-weight:800; line-height:1; min-width:58px; text-align:center; }
.centro { text-align:center; font-size:12px; color:#9aa4b2; min-width:104px; }
.parciales { width:100%; border-collapse:collapse; margin-top:14px; font-size:12px; }
.parciales th, .parciales td { padding:5px 6px; text-align:center; }
.parciales th { color:#9aa4b2; font-weight:500; border-bottom:1px solid #1f2937; }
.parciales td.tot { font-weight:800; color:#fff; }
.parciales td.eq { text-align:left; font-weight:600; }
.fila { display:flex; gap:12px; padding:10px 0; border-bottom:1px solid #1f2937; }
.fila:last-child { border-bottom:0; }
.fila .ico { width:20px; text-align:center; opacity:.8; }
.fila .val { font-weight:600; }
.fila .sub { color:#9aa4b2; font-size:12px; }
.foto { width:100%; border-radius:10px; margin-top:12px; display:block; }
.chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:6px; }
.chip { border:1px solid #374151; border-radius:8px; padding:3px 9px; font-size:12px; }
.arbitro { display:flex; gap:10px; padding:3px 0; }
.arbitro .rol { color:#9aa4b2; font-size:12px; min-width:72px; }
.jugada { display:grid; grid-template-columns:58px 1fr auto; gap:10px;
          padding:7px 0; border-bottom:1px solid #131c2b; font-size:13px; }
.jugada:last-child { border-bottom:0; }
.jugada .t { color:#9aa4b2; font-size:12px; }
.jugada .m { font-variant-numeric:tabular-nums; color:#9aa4b2; font-size:12px; }
.jugada.anota .tx { font-weight:600; }
.cuarto { background:#0f172a; border-radius:8px; padding:7px 10px; margin:8px 0;
          font-weight:700; font-size:12px; color:#9aa4b2; }
.vacio { color:#9aa4b2; padding:14px 0; }
@media (max-width:640px) {
  .equipo .nom { font-size:13px; } .equipo .pts { font-size:28px; }
  .wrap { padding:12px 10px 40px; }
}
"""

# Grafico: dos areas apiladas. El visita ocupa de la frontera al techo y el
# local de la frontera al suelo, asi que las dos se ven SIEMPRE y la frontera
# movil es la probabilidad.
_JS_GRAFICO = """
const COLOR_LOCAL="#7c3aed", COLOR_VISITA="#f97316", BASE=0.5;
const W=920,H=290,PL=44,PR=16,PT=14,PB=26;
function dibujar(datos,nombres){
  const host=document.getElementById("grafico");
  const pts=(datos||[]).filter(p=>typeof p.local==="number");
  if(pts.length<2){ host.innerHTML='<div class="vacio">Sin datos de probabilidad.</div>'; return; }
  const n=pts.length;
  const x=i=>PL+(i/(n-1))*(W-PL-PR);
  const y=v=>PT+(1-v)*(H-PT-PB);
  const suelo=y(0), techo=y(1);
  const abajo="M"+x(0)+","+suelo+" "+pts.map((p,i)=>"L"+x(i)+","+y(p.local)).join(" ")+" L"+x(n-1)+","+suelo+" Z";
  const arriba="M"+x(0)+","+techo+" "+pts.map((p,i)=>"L"+x(i)+","+y(p.local)).join(" ")+" L"+x(n-1)+","+techo+" Z";
  const frente=pts.map((p,i)=>(i?"L":"M")+x(i)+","+y(p.local)).join(" ");
  let cortes="";
  for(let i=1;i<n;i++){
    if(pts[i].periodo&&pts[i-1].periodo&&pts[i].periodo!==pts[i-1].periodo){
      cortes+='<line x1="'+x(i)+'" y1="'+PT+'" x2="'+x(i)+'" y2="'+(H-PB)+'" stroke="#000" stroke-opacity=".35"/>';
      cortes+='<text x="'+(x(i)+4)+'" y="'+(PT+11)+'" fill="#fff" fill-opacity=".5" font-size="10">'+pts[i].periodo+'</text>';
    }
  }
  const u=pts[n-1];
  host.innerHTML='<svg viewBox="0 0 '+W+' '+H+'" width="100%" role="img">'
    +'<path d="'+arriba+'" fill="'+COLOR_VISITA+'" fill-opacity=".72"/>'
    +'<path d="'+abajo+'" fill="'+COLOR_LOCAL+'" fill-opacity=".72"/>'
    +'<path d="'+frente+'" fill="none" stroke="#fff" stroke-opacity=".85" stroke-width="1.6"/>'
    +'<line x1="'+PL+'" y1="'+y(BASE)+'" x2="'+(W-PR)+'" y2="'+y(BASE)+'" stroke="#fff" stroke-opacity=".25" stroke-dasharray="5 5"/>'
    +cortes
    +'<text x="'+(PL+8)+'" y="'+((y(u.local)+techo)/2)+'" fill="#fff" fill-opacity=".9" font-size="12" font-weight="600">'+nombres.visita+'</text>'
    +'<text x="'+(PL+8)+'" y="'+((y(u.local)+suelo)/2+12)+'" fill="#fff" fill-opacity=".9" font-size="12" font-weight="600">'+nombres.local+'</text>'
    +'<circle cx="'+x(n-1)+'" cy="'+y(u.local)+'" r="4" fill="#fff"/>'
    +'<text x="'+PL+'" y="'+(H-6)+'" fill="#6b7280" font-size="10">Inicio</text>'
    +'<text x="'+(W-PR)+'" y="'+(H-6)+'" fill="#6b7280" font-size="10" text-anchor="end">'+(u.reloj||"Final")+' · '+(u.periodo==null?"-":u.periodo+"º")+'</text>'
    +'<rect id="hover" x="'+PL+'" y="'+PT+'" width="'+(W-PL-PR)+'" height="'+(H-PT-PB)+'" fill="transparent"/>'
    +'<line id="cur" y1="'+PT+'" y2="'+(H-PB)+'" stroke="#fff" stroke-opacity=".6" opacity="0"/>'
    +'</svg>';
  const svg=host.querySelector("svg"), zona=document.getElementById("hover"), cur=document.getElementById("cur");
  const tip=document.getElementById("tip");
  zona.addEventListener("mousemove",function(ev){
    const caja=svg.getBoundingClientRect();
    const vx=(ev.clientX-caja.left)*(W/caja.width);
    let t=(vx-PL)/(W-PL-PR); t=Math.max(0,Math.min(1,t));
    const i=Math.round(t*(n-1)), p=pts[i];
    cur.setAttribute("x1",x(i)); cur.setAttribute("x2",x(i)); cur.setAttribute("opacity","1");
    tip.innerHTML="<b>"+(p.local*100).toFixed(1)+"%</b> local<br>"
      +(p.marcador_visita==null?"?":p.marcador_visita)+"-"+(p.marcador_local==null?"?":p.marcador_local)
      +" · "+(p.periodo==null?"-":p.periodo+"º")+" "+(p.reloj||"")
      +'<br><span style="color:#9aa4b2">'+(p.jugada||"")+"</span>";
    tip.style.display="block";
    tip.style.left=Math.min(window.innerWidth-330,ev.clientX+14)+"px";
    tip.style.top=(ev.clientY-64)+"px";
  });
  zona.addEventListener("mouseleave",function(){
    tip.style.display="none"; cur.setAttribute("opacity","0");
  });
}
"""


def _esc(valor) -> str:
    return escape(str(valor if valor is not None else ""))


def _fecha_local(iso: str) -> str:
    """'2026-11-13T00:00Z' -> '12 nov 2026 · 6:00 PM' (Nicaragua, UTC-6).

    ESPN manda la fecha en UTC; el usuario lee hora local. Se resta el desfase
    fijo de Nicaragua para no meter dependencias de zona horaria.
    """
    from datetime import datetime, timedelta

    try:
        utc = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return str(iso or "")
    local = utc - timedelta(hours=6)
    meses = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep",
             "oct", "nov", "dic")
    hora = local.strftime("%I:%M %p").lstrip("0")
    return f"{local.day} {meses[local.month - 1]} {local.year} · {hora}"


def _equipos(detalle: dict):
    equipos = detalle.get("teams") or []
    local = next((t for t in equipos if t.get("homeAway") == "home"), None)
    visita = next((t for t in equipos if t.get("homeAway") == "away"), None)
    if local is None and equipos:
        local = equipos[0]
    if visita is None and len(equipos) > 1:
        visita = equipos[1]
    return local or {}, visita or {}


def _record(t: dict) -> str:
    """'9-3 · 5-1 Local' a partir de records_by_type.

    ESPN usa 'home' para el local y 'road'/'away' para el visitante; se traduce
    para que la ficha se lea como en su web.
    """
    r = t.get("records_by_type") or {}
    partes = []
    if r.get("total"):
        partes.append(str(r["total"]))
    if t.get("homeAway") == "home" and r.get("home"):
        partes.append(f"{r['home']} Local")
    if t.get("homeAway") == "away":
        for clave in ("road", "away"):
            if r.get(clave):
                partes.append(f"{r[clave]} Visitante")
                break
    return " · ".join(partes)


def _cuarto(n) -> str:
    """3 -> '3er cuarto'. Se escribe el cuarto, no 'Q3'.

    ESPN manda '3rd Quarter' en ingles: mostrarlo tal cual deja la ficha a
    medias, y poner 'Q3' no pega con el marcador (que usa 1 2 3 4 T).
    """
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    if n == 1:
        return "1er cuarto"
    if n == 2:
        return "2do cuarto"
    if n == 3:
        return "3er cuarto"
    if n == 4:
        return "4to cuarto"
    return f"Prórroga {n - 4}"


def _bloque_marcador(detalle: dict) -> str:
    """Marcador con récords y parciales por cuarto."""
    local, visita = _equipos(detalle)

    def _lado(t, clase):
        logo = t.get("logo") or ""
        img = f'<img src="{_esc(logo)}" alt="">' if logo else ""
        return (
            f'<div class="equipo {clase}">{img}'
            f'<div><div class="nom">{_esc(t.get("name"))}</div>'
            f'<div class="rec">{_esc(_record(t))}</div></div>'
            f'<div class="pts">{_esc(t.get("score"))}</div></div>'
        )

    cuartos = max(len(local.get("linescores") or []),
                  len(visita.get("linescores") or []))
    cabecera = "".join(f"<th>{i + 1}</th>" for i in range(cuartos)) + "<th>T</th>"

    def _fila(t):
        lineas = list(t.get("linescores") or [])
        celdas = "".join(f"<td>{_esc(v)}</td>" for v in lineas)
        celdas += "<td>-</td>" * max(0, cuartos - len(lineas))
        return (f'<tr><td class="eq">{_esc(t.get("abbr") or t.get("name"))}</td>'
                f'{celdas}<td class="tot">{_esc(t.get("score"))}</td></tr>')

    estado = _esc(detalle.get("status"))
    reloj = detalle.get("clock") or ""
    periodo = detalle.get("period")
    medio = (f"{_esc(reloj)} · {_esc(_cuarto(periodo))}" if reloj and periodo
             else estado or "Programado")

    parciales = ""
    if cuartos:
        parciales = (f'<table class="parciales"><thead><tr><th></th>{cabecera}</tr>'
                     f'</thead><tbody>{_fila(visita)}{_fila(local)}</tbody></table>')

    return (f'<div class="marcador">{_lado(visita, "visita")}'
            f'<div class="centro">{medio}<br>{estado}</div>'
            f'{_lado(local, "local")}</div>{parciales}')


def _bloque_ficha(detalle: dict) -> str:
    """GAME INFORMATION: fecha, sede con foto, asistencia y dónde verlo."""
    gi = detalle.get("game_info") or {}
    if not gi.get("venue") and not gi.get("broadcasts") and not gi.get("image"):
        return ""
    filas = []
    fecha = _fecha_local(detalle.get("date") or "")
    if fecha:
        filas.append(f'<div class="fila"><div class="ico">🗓️</div><div>'
                     f'<div class="val">{_esc(fecha)}</div></div></div>')
    sede = gi.get("venue") or ""
    ciudad = ", ".join(x for x in (gi.get("city"), gi.get("state")) if x)
    if sede or ciudad:
        extra = (f"{gi.get('attendance')} espectadores"
                 if gi.get("attendance") else "")
        filas.append(
            f'<div class="fila"><div class="ico">📍</div><div>'
            f'<div class="val">{_esc(sede)}</div>'
            f'<div class="sub">{_esc(ciudad)}{" · " + extra if extra else ""}'
            f'</div></div></div>')
    if gi.get("image"):
        filas.append(f'<img class="foto" src="{_esc(gi["image"])}" alt="Estadio">')
    tv = gi.get("broadcasts") or []
    if tv:
        chips = "".join(f'<span class="chip">{_esc(b.get("name"))}</span>' for b in tv)
        filas.append(f'<div class="fila"><div class="ico">📺</div><div>'
                     f'<div class="val">Dónde verlo</div>'
                     f'<div class="chips">{chips}</div></div></div>')
    return f'<div class="card"><h2>Información del partido</h2>{"".join(filas)}</div>'


def _bloque_arbitros(detalle: dict) -> str:
    """OFFICIATING CREW: los tres árbitros con su rol."""
    arbitros = (detalle.get("game_info") or {}).get("officials") or []
    if not arbitros:
        return ""
    filas = "".join(
        f'<div class="arbitro"><span class="rol">{_esc(a.get("role") or "Árbitro")}'
        f'</span><span>{_esc(a.get("name"))}</span></div>'
        for a in arbitros
    )
    return f'<div class="card"><h2>Oficiales</h2>{filas}</div>'


def _bloque_jugadas(detalle: dict, limite: int = 40) -> str:
    """Jugada a jugada, de la más reciente a la más antigua.

    El cuarto va SOLO en el separador (en español) y cada jugada muestra
    únicamente el reloj: repetir "Q3" en las 40 filas era ruido.
    """
    jugadas = (detalle.get("plays") or [])[-limite:]
    if not jugadas:
        return ""
    partes = []
    cuarto_previo = None
    for j in reversed(jugadas):
        periodo = j.get("periodo")
        if periodo and periodo != cuarto_previo:
            partes.append(f'<div class="cuarto">{_esc(_cuarto(periodo))}</div>')
            cuarto_previo = periodo
        clase = "jugada anota" if j.get("anota") else "jugada"
        marcador = f'{j.get("visita")}-{j.get("local")}'
        partes.append(
            f'<div class="{clase}">'
            f'<div class="t">{_esc(j.get("reloj"))}</div>'
            f'<div class="tx">{_esc(j.get("texto"))}</div>'
            f'<div class="m">{_esc(marcador)}</div></div>'
        )
    return (f'<div class="card"><h2>Jugada a jugada '
            f'<span class="muted" style="text-transform:none;font-weight:400">'
            f'(últimas {len(jugadas)})</span></h2>{"".join(partes)}</div>')


_PLANTILLA = Template("""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>$titulo</title><style>$css</style></head>
<body><div class="wrap">
  <div class="card">
    <h1>$titulo</h1>
    <div class="muted">$subtitulo</div>
    $marcador
  </div>
  $ficha
  $arbitros
  $grafico
  $jugadas
</div>
<div id="tip" style="position:fixed;display:none;background:#0b0f19;
     border:1px solid #374151;border-radius:8px;padding:7px 9px;font-size:12px;
     max-width:310px;pointer-events:none;z-index:9"></div>
<script>
  const SERIE = $serie_json;
  const NOMBRES = $nombres_json;
$js
  dibujar(SERIE, NOMBRES);
</script>
</body></html>""")


def pagina(detalle: dict) -> str:
    """HTML completo de la página de estadísticas de UN partido."""
    local, visita = _equipos(detalle)
    nombre_local = local.get("abbr") or local.get("name") or "Local"
    nombre_visita = visita.get("abbr") or visita.get("name") or "Visitante"

    serie = [p for p in (detalle.get("win_probability") or [])
             if isinstance(p, dict) and isinstance(p.get("local"), (int, float))]

    grafico = ""
    if len(serie) > 1:
        resumen = detalle.get("win_probability_resumen") or {}
        pie = ""
        if resumen:
            pie = (f'<div class="muted" style="font-size:12px;margin-top:6px">'
                   f'Máx. {_esc(nombre_visita)} '
                   f'{round(resumen.get("max_visita", 0) * 100)}% · '
                   f'Máx. {_esc(nombre_local)} '
                   f'{round(resumen.get("max_local", 0) * 100)}%</div>')
        grafico = (f'<div class="card"><h2>Probabilidad de victoria</h2>'
                   f'<div id="grafico"></div>{pie}</div>')

    titulo = f'{local.get("name") or "Local"} vs {visita.get("name") or "Visitante"}'
    liga = detalle.get("league") or detalle.get("label") or "NBA"
    subtitulo = f'{_esc(liga)} · {_esc(detalle.get("status") or "")}'
    if detalle.get("state") == "in":
        subtitulo += f' · {_esc(_cuarto(detalle.get("period")))} {_esc(detalle.get("clock") or "")}'

    return _PLANTILLA.substitute(
        titulo=_esc(titulo),
        subtitulo=subtitulo,
        css=_CSS,
        marcador=_bloque_marcador(detalle),
        ficha=_bloque_ficha(detalle),
        arbitros=_bloque_arbitros(detalle),
        grafico=grafico,
        jugadas=_bloque_jugadas(detalle),
        serie_json=json.dumps(serie),
        nombres_json=json.dumps({"local": nombre_local, "visita": nombre_visita}),
        js=_JS_GRAFICO,
    )


__all__ = ["pagina"]




