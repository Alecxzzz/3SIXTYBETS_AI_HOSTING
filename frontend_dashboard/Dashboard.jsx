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
  const [vista, setVista] = useState("dia"); // "dia" | "acertados"
  const [error, setError] = useState(null);
  const [compartiendo, setCompartiendo] = useState(null);

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

  if (error) return <div className="dash-error">No se pudo cargar el dashboard: {error}</div>;
  if (!data) return <div className="dash-loading">Cargando dashboard...</div>;

  const picks =
    vista === "dia"
      ? data.pronosticos_del_dia || []
      : data.pronosticos_acertados || [];

  const esGolden = (p) =>
    (p.tier || "") === "GOLDEN PICK" ||
    (p.odds != null && p.odds >= 1.35 && p.odds <= 1.4);

  return (
    <div className="dashboard">
      <h1 className="dash-welcome">{data.welcome} 👋</h1>

      {/* STATS HORIZONTALES */}
      <div className="dash-stats">
        <button
          className={`dash-stat ${vista === "dia" ? "activa" : ""}`}
          onClick={() => setVista("dia")}
        >
          <span className="dash-stat-num">{data.stats.pronosticos_del_dia}</span>
          <span className="dash-stat-label">Pronósticos del día</span>
        </button>
        <button
          className={`dash-stat ${vista === "acertados" ? "activa" : ""}`}
          onClick={() => setVista("acertados")}
        >
          <span className="dash-stat-num">
            {data.stats.pronosticos_acertados_por_la_ia}
          </span>
          <span className="dash-stat-label">Pronósticos acertados por la IA</span>
        </button>
      </div>

      {/* LISTA DE PICKS (horizontal) */}
      <div className="dash-picks">
        {picks.length === 0 && (
          <p className="dash-vacio">
            {vista === "dia"
              ? "La IA aún no generó pronósticos hoy. Vuelve en unos minutos."
              : "Aún no hay pronósticos acertados hoy."}
          </p>
        )}
        {picks.map((p) => (
          <div key={p.id} className={`dash-pick${esGolden(p) ? " golden" : ""}`}>
            <div className="dash-pick-head">
              <span className="dash-pick-sport">{p.sportLabel}</span>
              {esGolden(p) ? (
                <span className="dash-pick-golden">GOLDEN PICK</span>
              ) : (
                p.result === "ACIERTO" && <span className="dash-pick-win">✅ ACIERTO</span>
              )}
            </div>
            <div className="dash-pick-event">{p.eventName}</div>
            <div className="dash-pick-market">
              <strong>{p.market}</strong>
              <span className="dash-pick-sel">{p.selection}</span>
            </div>
            <div className="dash-pick-foot">
              {p.odds ? <span>Cuota {p.odds}</span> : null}
              {p.confidence ? <span>Confianza {p.confidence}</span> : null}
              {p.verificado >= 2 ? <span className="dash-verif">x2 verificado</span> : null}
              {p.rationale ? <p className="dash-pick-edge">{p.rationale}</p> : null}
            </div>
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
          </div>
        ))}
      </div>
    </div>
  );
}
