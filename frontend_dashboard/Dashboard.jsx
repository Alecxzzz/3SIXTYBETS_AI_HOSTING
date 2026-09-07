/**
 * Dashboard - 3SIXTYBETS
 * Pagina principal: bienvenida + stats (pronosticos del dia / acertados por la IA).
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

export default function Dashboard() {
  const [data, setData] = useState(null);
  const [vista, setVista] = useState("dia"); // "dia" | "acertados"
  const [error, setError] = useState(null);

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
          <div key={p.id} className="dash-pick">
            <div className="dash-pick-head">
              <span className="dash-pick-sport">{p.sportLabel}</span>
              {p.result === "ACIERTO" && <span className="dash-pick-win">✅ ACIERTO</span>}
            </div>
            <div className="dash-pick-event">{p.eventName}</div>
            <div className="dash-pick-market">
              <strong>{p.market}</strong>
              <span className="dash-pick-sel">{p.selection}</span>
            </div>
            <div className="dash-pick-foot">
              {p.odds ? <span>Cuota {p.odds}</span> : null}
              {p.confidence ? <span>Confianza {p.confidence}</span> : null}
              {p.rationale ? <p className="dash-pick-edge">{p.rationale}</p> : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
