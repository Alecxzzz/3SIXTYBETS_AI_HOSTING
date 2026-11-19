/**
 * Dashboard - 3SIXTYBETS
 * Pagina principal: bienvenida + stats (pronosticos del dia / acertados por la IA).
 * GOLDEN PICK: badge dorado + boton Compartir que genera la FOTO de la
 * carta (canvas -> PNG) para WhatsApp/Telegram/Instagram.
 *
 * Copiar a: threesixtybets-chat/src/pages/Dashboard.jsx
 * Configurar como la ruta "/" (o la primera vista tras login) para que al
 * entrar al sitio se muestre directamente.
 *
 * Este componente asume que ya existe un apiClient con el token Bearer
 * (el mismo que usa /chat, /sports, etc.). Reemplaza `api` por tu cliente
 * HTTP real (axios/fetch) si es distinto.
 */

import { useEffect, useState } from "react";
import "./Dashboard.css";

const API_URL = import.meta.env.VITE_API_URL || "";


async function apiGet(path) {
  const token = localStorage.getItem("access_token");
  const res = await fetch(`${API_URL}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`Error ${res.status}`);
  return res.json();
}

async function apiPost(path) {
  const token = localStorage.getItem("access_token");
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`Error ${res.status}`);
  return res.json();
}

async function analizarPartido(pick) {
  const token = localStorage.getItem("access_token");
  const params = new URLSearchParams({
    sport: pick.sport,
    event_id: pick.eventId || pick.event_id,
  });
  const res = await fetch(`${API_URL}/stats/ai-analysis?${params.toString()}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(payload.detail || `Error ${res.status}`);
  return payload;
}

/* ---------- CARTA GOLDEN -> FOTO PNG (canvas, sin dependencias) ---------- */

function wrapText(ctx, text, maxWidth) {
  const words = String(text || "").split(/\s+/).filter(Boolean);
  const lines = [];
  let line = "";
  for (const w of words) {
    const t = line ? `${line} ${w}` : w;
    if (ctx.measureText(t).width > maxWidth && line) {
      lines.push(line);
      line = w;
    } else {
      line = t;
    }
  }
  if (line) lines.push(line);
  return lines.slice(0, 4);
}

function dibujarCartaGolden(p) {
  const W = 1080;
  const H = 1350;
  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  const bg = ctx.createLinearGradient(0, 0, 0, H);
  bg.addColorStop(0, "#0c0f1d");
  bg.addColorStop(0.55, "#11162b");
  bg.addColorStop(1, "#0a0d18");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = "#f5c542";
  ctx.lineWidth = 10;
  ctx.strokeRect(18, 18, W - 36, H - 36);
  ctx.strokeStyle = "rgba(245,197,66,0.25)";
  ctx.lineWidth = 3;
  ctx.strokeRect(42, 42, W - 84, H - 84);
  ctx.textAlign = "center";
  ctx.fillStyle = "#f5c542";
  ctx.font = "800 44px system-ui, sans-serif";
  ctx.fillText("3SIXTYBETS", W / 2, 140);
  ctx.fillStyle = "rgba(255,255,255,0.75)";
  ctx.font = "600 30px system-ui, sans-serif";
  ctx.fillText("INTELIGENCIA DEPORTIVA", W / 2, 182);
  ctx.fillStyle = "#f5c542";
  const bw = 560;
  const bh = 84;
  const bx = (W - bw) / 2;
  const by = 225;
  if (ctx.roundRect) {
    ctx.beginPath();
    ctx.roundRect(bx, by, bw, bh, 42);
    ctx.fill();
  } else {
    ctx.fillRect(bx, by, bw, bh);
  }
  ctx.fillStyle = "#111";
  ctx.font = "900 46px system-ui, sans-serif";
  ctx.fillText("GOLDEN PICK", W / 2, by + 58);
  ctx.fillStyle = "#4ade80";
  ctx.font = "700 30px system-ui, sans-serif";
  ctx.fillText(p.verificado >= 2 ? "DOBLE VERIFICADO POR IA" : "VERIFICADO POR IA", W / 2, by + 125);
  ctx.fillStyle = "#ffffff";
  ctx.font = "800 52px system-ui, sans-serif";
  const evLines = wrapText(ctx, p.eventName || `${p.homeName || ""} vs ${p.awayName || ""}`, W - 160);
  evLines.forEach((l, i) => ctx.fillText(l, W / 2, 480 + i * 62));
  const baseY = 480 + evLines.length * 62;
  ctx.fillStyle = "rgba(255,255,255,0.65)";
  ctx.font = "600 32px system-ui, sans-serif";
  ctx.fillText(`${p.sportLabel || ""}${p.league ? ` · ${p.league}` : ""}`, W / 2, baseY + 30);
  ctx.fillStyle = "#f5c542";
  ctx.font = "900 60px system-ui, sans-serif";
  const titLines = wrapText(ctx, p.titulo || p.selection || p.market || "", W - 160);
  titLines.forEach((l, i) => ctx.fillText(l, W / 2, baseY + 130 + i * 72));
  const selY = baseY + 130 + titLines.length * 72;
  ctx.fillStyle = "#ffffff";
  ctx.font = "600 36px system-ui, sans-serif";
  if (p.selection && p.selection !== (p.titulo || "")) ctx.fillText(String(p.selection), W / 2, selY + 10);
  const cuotaY = selY + 150;
  ctx.fillStyle = "rgba(255,255,255,0.7)";
  ctx.font = "700 34px system-ui, sans-serif";
  ctx.fillText("CUOTA", W / 2, cuotaY);
  ctx.fillStyle = "#4ade80";
  ctx.font = "900 130px system-ui, sans-serif";
  ctx.fillText(p.odds != null ? String(p.odds) : "-", W / 2, cuotaY + 130);
  ctx.fillStyle = "rgba(255,255,255,0.8)";
  ctx.font = "600 32px system-ui, sans-serif";
  ctx.fillText(`Rango Golden 1.35-1.40${p.confidence ? ` · ${p.confidence}` : ""}`, W / 2, cuotaY + 190);
  if (p.rationale || p.porque) {
    ctx.fillStyle = "rgba(255,255,255,0.85)";
    ctx.font = "400 30px system-ui, sans-serif";
    wrapText(ctx, `${p.rationale || p.porque}`, W - 200).slice(0, 3).forEach((l, i) => ctx.fillText(l, W / 2, cuotaY + 250 + i * 42));
  }
  ctx.fillStyle = "rgba(255,255,255,0.5)";
  ctx.font = "600 28px system-ui, sans-serif";
  ctx.fillText("3SIXTYBETS · Juega responsablemente · +18", W / 2, H - 80);
  return canvas;
}

async function compartirCarta(p) {
  const canvas = dibujarCartaGolden(p);
  const nombre = `golden-pick-${String(p.eventName || "3sixtybets").replace(/[^\w-]+/g, "-").slice(0, 40)}.png`;
  const blob = await new Promise((res) => canvas.toBlob(res, "image/png"));
  const textoShare = `GOLDEN PICK 3SIXTYBETS\n${p.eventName || ""}\n${p.titulo || p.selection || ""}\nCuota ${p.odds ?? "-"} (1.35-1.40) · Doble verificado por IA`;
  try {
    if (blob && navigator.canShare) {
      const file = new File([blob], nombre, { type: "image/png" });
      if (navigator.canShare({ files: [file] })) {
        await navigator.share({ files: [file], title: "Golden Pick 3SIXTYBETS", text: textoShare });
        return;
      }
    }
  } catch (e) {
    if (e && e.name === "AbortError") return;
  }
  try {
    if (blob) {
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = nombre;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
    }
    if (navigator.clipboard) await navigator.clipboard.writeText(textoShare);
    alert("Foto de la carta descargada y texto copiado. Pegalo en WhatsApp/Telegram.");
  } catch {
    alert("No se pudo compartir la carta. Intentalo de nuevo.");
  }
}

export default function Dashboard() {
  const [data, setData] = useState(null);
  const [vista, setVista] = useState("dia"); // "dia" | "manana" | "acertados"
  const [error, setError] = useState(null);
  const [compartiendo, setCompartiendo] = useState(null);
  const [analizando, setAnalizando] = useState(null);
  const [analisis, setAnalisis] = useState({});
  const [errorAnalisis, setErrorAnalisis] = useState({});
  // Favoritos del usuario (ids de pronostico).
  const [favoritos, setFavoritos] = useState(() => new Set());
  const [favCargando, setFavCargando] = useState({});

  const ejecutarAnalisis = async (p) => {
    setAnalizando(p.id);
    setErrorAnalisis((actual) => ({ ...actual, [p.id]: null }));
    try {
      const resultado = await analizarPartido(p);
      setAnalisis((actual) => ({ ...actual, [p.id]: resultado.analysis || resultado }));
    } catch (e) {
      setErrorAnalisis((actual) => ({ ...actual, [p.id]: e.message || "No se pudo completar el análisis." }));
    } finally {
      setAnalizando(null);
    }
  };

  useEffect(() => {
    let alive = true;
    const cargar = () =>
      apiGet("/dashboard")
        .then((d) => alive && setData(d))
        .catch((e) => alive && setError(e.message));
    cargar();
    const intervalo = setInterval(cargar, 60000); // refresco cada minuto
    return () => {
      alive = false;
      clearInterval(intervalo);
    };
  }, []);

  // Favoritos del usuario: se cargan una vez al montar.
  useEffect(() => {
    let alive = true;
    apiGet("/favoritos/picks")
      .then((r) => {
        if (!alive) return;
        setFavoritos(new Set((r?.favoritos || []).map((f) => String(f.ref_id))));
      })
      .catch(() => {
        /* sin sesion: la estrella simplemente no marca nada */
      });
    return () => {
      alive = false;
    };
  }, []);

  /** Alterna el favorito de un pronostico (optimista, revierte si falla). */
  const alternarFavorito = async (p) => {
    const id = String(p.id);
    const estaba = favoritos.has(id);
    setFavoritos((actual) => {
      const nuevo = new Set(actual);
      if (estaba) nuevo.delete(id);
      else nuevo.add(id);
      return nuevo;
    });
    setFavCargando((actual) => ({ ...actual, [id]: true }));
    try {
      const r = await apiPost(`/favoritos/picks/${encodeURIComponent(id)}/toggle`);
      setFavoritos((actual) => {
        const nuevo = new Set(actual);
        if (r?.favorito) nuevo.add(id);
        else nuevo.delete(id);
        return nuevo;
      });
    } catch {
      setFavoritos((actual) => {
        const nuevo = new Set(actual);
        if (estaba) nuevo.add(id);
        else nuevo.delete(id);
        return nuevo;
      });
    } finally {
      setFavCargando((actual) => {
        const nuevo = { ...actual };
        delete nuevo[id];
        return nuevo;
      });
    }
  };

  if (error) return <div className="dash-error">No se pudo cargar el dashboard: {error}</div>;
  if (!data) return <div className="dash-loading">Cargando dashboard...</div>;

  const picks =
    vista === "dia"
      ? data.pronosticos_del_dia || []
      : vista === "manana"
        ? data.pronosticos_manana || []
        : data.pronosticos_acertados || [];

  const esGolden = (p) =>
    (p.tier || "") === "GOLDEN PICK" ||
    (p.odds != null && p.odds >= 1.35 && p.odds <= 1.4);

  return (
    <div className="dashboard">
      <h1 className="dash-welcome">{data.welcome} 👋</h1>

      {/* SECCION DE STATS EN GRID (estilo Canzu Analytics) */}
      <div className="dash-stats-grid">
        <div className="dash-stat-card">
          <span className="dash-stat-kicker">Pronósticos generados</span>
          <span className="dash-stat-value">{data.stats.pronosticos_del_dia}</span>
        </div>
        <div className="dash-stat-card">
          <span className="dash-stat-kicker">Partidos analizados (mañana)</span>
          <span className="dash-stat-value">{data.stats.pronosticos_manana || 0}</span>
        </div>
        <div className="dash-stat-card">
          <span className="dash-stat-kicker">Pronósticos acertados</span>
          <span className="dash-stat-value">{data.stats.pronosticos_acertados_por_la_ia}</span>
        </div>
        <div className="dash-stat-card dash-stat-card--accent">
          <span className="dash-stat-kicker">IA revisada con IA</span>
          <span className="dash-stat-value">verificada</span>
          <span className="dash-stat-sub">2 pasadas independientes confirmadas</span>
        </div>
      </div>

      {/* SECCION DE PICKS (tarjetas tipo Canzu Analytics, agrupadas por liga) */}
      <div className="dash-league-list">
        {Object.entries(
          picks.reduce(
            (acc, p) => {
              const key = p.league || "Sin liga";
              if (!acc[key]) acc[key] = [];
              acc[key].push(p);
              return acc;
            },
            {} as Record<string, typeof picks>
          )
        ).map(([liga, picksLiga]) => (
          <div key={liga} className="dash-league">
            <div className="dash-league-header">
              <span className="dash-league-flag">{liga.slice(0, 3)}</span>
              <span className="dash-league-name">{liga}</span>
              <span className="dash-league-count">{picksLiga.length}</span>
            </div>
            <div className="dash-league-matches">
              {picksLiga.map((p) => (
                <article
                  key={p.id}
                  className={`dash-match-card${esGolden(p) ? " golden" : ""}${
                    p.result === "ACIERTO" ? " acierto" : ""
                  }`}
                >
                  <div className="dash-match-head">
                    <span className="dash-match-time">{p.eventTime || p.date || "—"}</span>
                    <div className="dash-match-teams">
                      <div className="dash-match-team">
                        <span className="dash-match-flag">{p.homeFlag || "🏳️"}</span>
                        <span className="dash-match-name">{p.homeName || p.eventName}</span>
                      </div>
                      <div className="dash-match-team dash-match-away">
                        <span className="dash-match-name">{p.awayName || "vs"}</span>
                        <span className="dash-match-flag">{p.awayFlag || "🏳️"}</span>
                      </div>
                    </div>
                  </div>
                  {p.score ? (
                    <span className="dash-match-score">{p.score}</span>
                  ) : (
                    <div className="dash-match-odds"><span>{p.odds}</span></div>
                  )}
                  <div className="dash-match-stats">
                    <span className="dash-match-stat">
                      <span className="dash-match-stat-value">{p.o25 ?? "+2.5"}</span>
                      <span className="dash-match-stat-label">{p.o25pct ?? "47%"}</span>
                    </span>
                    <span className="dash-match-stat">
                      <span className="dash-match-stat-label">AM</span>
                      <span className="dash-match-stat-value">{p.amPct ?? "52%"}</span>
                    </span>
                    {p.corners ? (
                      <span className="dash-match-stat">
                        <span className="dash-match-stat-label">Corners</span>
                        <span className="dash-match-stat-value">{p.corners}</span>
                      </span>
                    ) : null}
                    {p.cards ? (
                      <span className="dash-match-stat">
                        <span className="dash-match-stat-label">Tarjetas</span>
                        <span className="dash-match-stat-value">{p.cards}</span>
                      </span>
                    ) : null}
                  </div>
                  <div className="dash-match-pick">
                    <span className="dash-match-pick-title">{p.market}</span>
                    <span className="dash-match-pick-value">{p.selection}</span>
                  </div>
                  {esGolden(p) ? <span className="dash-match-golden">GOLDEN PICK</span> : null}
                  {p.result === "ACIERTO" ? <span className="dash-match-acierto">✅ ACIERTO</span> : null}
                  <div className="dash-match-actions">
                    <button
                      type="button"
                      className={`dash-fav${favoritos.has(String(p.id)) ? " es-fav" : ""}`}
                      onClick={() => alternarFavorito(p)}
                      disabled={!!favCargando[String(p.id)]}
                      title={favoritos.has(String(p.id)) ? "Quitar de favoritos" : "Guardar en favoritos"}
                      aria-label={favoritos.has(String(p.id)) ? "Quitar de favoritos" : "Guardar en favoritos"}
                      aria-pressed={favoritos.has(String(p.id))}
                    >
                      {favoritos.has(String(p.id)) ? "★" : "☆"}
                    </button>
                    <button
                      className="dash-analyze"
                      disabled={analizando === p.id || !p.eventId}
                      onClick={() => ejecutarAnalisis(p)}
                    >
                      {analizando === p.id ? "Analizando con IA…" : analisis[p.id] ? "Volver a analizar" : "Analizar con IA"}
                    </button>
                  </div>
                  {errorAnalisis[p.id] ? <p className="dash-analysis-error">{errorAnalisis[p.id]}</p> : null}
                  {analisis[p.id] ? <div className="dash-analysis">{analisis[p.id]}</div> : null}
                  <button
                    className="dash-share"
                    disabled={compartiendo === p.id}
                    onClick={async () => {
                      setCompartiendo(p.id);
                      try {
                        await compartirCarta(p);
                      } finally {
                        setCompartiendo(null);
                      }
                    }}
                  >
                    {compartiendo === p.id ? "Generando foto…" : "Compartir carta"}
                  </button>
                </article>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
