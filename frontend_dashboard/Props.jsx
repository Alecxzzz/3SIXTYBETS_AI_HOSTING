/**
 * Props - 3SIXTYBETS
 * Apartado de props de jugador: para cada prop que publica el bookmaker,
 * muestra en cuantos de sus ultimos N partidos el jugador cumplio la linea,
 * la cuota real y el edge (% real - % implicito en la cuota).
 *
 * Copiar a: threesixtybets-chat/src/pages/Props.jsx
 * Consume GET /api/props?sport=soccer|nba|nfl (backend FastAPI).
 *
 * Los datos los prepara el backend con props_scheduler (cada 90 min): la
 * pagina solo lee, asi que responde al instante.
 */

import { useEffect, useState } from "react";
import "./Props.css";

const API_URL = import.meta.env.VITE_API_URL || "";

const DEPORTES = [
  { key: "soccer", label: "Futbol" },
  { key: "nba", label: "NBA" },
  { key: "nfl", label: "NFL" },
];

async function apiGet(path) {
  const token = localStorage.getItem("access_token");
  const res = await fetch(`${API_URL}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
  return data;
}

/* Una sola fila de barras: una por partido. Altura = lo que hizo ese partido,
   color = si supero la linea (verde) o no (rojo). */
function Barras({ valores, linea, lado }) {
  if (!valores || valores.length === 0) {
    return <p className="props-sin-datos">Sin historico reciente</p>;
  }
  const max = Math.max(linea || 0, ...valores, 1);
  return (
    <div className="props-barras" title={`Ultimos ${valores.length} partidos`}>
      {valores.map((valor, i) => {
        const cumple = lado === "under" ? valor <= linea : valor >= linea;
        const alto = Math.max(6, Math.round((valor / max) * 46));
        return (
          <div
            key={i}
            className={`props-barra ${cumple ? "ok" : "no"}`}
            style={{ height: `${alto}px` }}
            title={`${valor}`}
          />
        );
      })}
    </div>
  );
}

export default function Props() {
  const [sport, setSport] = useState("soccer");
  const [estado, setEstado] = useState("cargando");
  const [props, setProps] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let vivo = true;
    setEstado("cargando");
    setError("");
    apiGet(`/api/props?sport=${sport}`)
      .then((data) => {
        if (!vivo) return;
        setProps(data.props || []);
        setEstado("ok");
      })
      .catch((err) => {
        if (!vivo) return;
        setError(err.message || "No se pudieron cargar los props.");
        setEstado("error");
      });
    return () => {
      vivo = false;
    };
  }, [sport]);

  return (
    <div className="props-page">
      <h1 className="props-title">Props de jugador</h1>
      <p className="props-sub">
        En cuantos de sus ultimos 5 partidos el jugador cumplio la linea,
        frente a la cuota que paga el bookmaker.
      </p>

      <div className="props-tabs" role="tablist">
        {DEPORTES.map((d) => (
          <button
            key={d.key}
            type="button"
            role="tab"
            aria-selected={sport === d.key}
            className={`props-tab ${sport === d.key ? "on" : ""}`}
            onClick={() => setSport(d.key)}
          >
            {d.label}
          </button>
        ))}
      </div>

      {estado === "cargando" && <p className="props-sub">Cargando props...</p>}
      {estado === "error" && <p className="props-error">{error}</p>}
      {estado === "ok" && props.length === 0 && (
        <p className="props-sub">
          No hay props de jugador publicados ahora mismo en este deporte.
        </p>
      )}

      <div className="props-lista">
        {props.map((p) => (
          <article
            className="props-card"
            key={p.id || `${p.jugador}-${p.prop}-${p.linea}-${p.lado}`}
          >
            {p.foto ? (
              <img
                className="props-foto"
                src={p.foto}
                alt={p.jugador}
                loading="lazy"
                onError={(ev) => {
                  ev.currentTarget.style.visibility = "hidden";
                }}
              />
            ) : (
              <div className="props-foto" />
            )}

            <div className="props-info">
              <div className="props-nombre">
                {p.jugador}
                {p.equipo ? <span className="props-equipo">({p.equipo})</span> : null}
              </div>
              <div className="props-prop">
                {p.prop_raw || p.prop} - {p.lado === "under" ? "MENOS" : "MAS"} de {p.linea}
              </div>
              <Barras valores={p.valores} linea={p.linea} lado={p.lado} />
              <div className="props-racha">
                {p.cumple ?? 0}/{p.n_partidos ?? 0} partidos
                {p.pct != null ? ` (${p.pct}%)` : ""}
              </div>
            </div>

            <div className="props-datos">
              <div className="props-cuota">{p.cuota ?? "-"}</div>
              <div className="props-impl">impl. {p.implicita ?? "-"}%</div>
              {p.edge != null ? (
                <div className={`props-edge ${p.edge >= 0 ? "pos" : "neg"}`}>
                  edge {p.edge > 0 ? "+" : ""}
                  {p.edge}%
                </div>
              ) : null}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
