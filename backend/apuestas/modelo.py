"""
Modelo estadistico para predecir probabilidad de acierto de un pick.
===============================================================

Por que NO un modelo de lenguaje: hoy la IA elige mercado basandose en su
ultima forma y acierta al azar. Medido sobre los 238 picks de futbol
resueltos: 52,3% de acierto con cuota media 1,70, es decir ROI -11%. Ningun
LLM arregla eso; lo que hace falta es estimar la probabilidad real y
compararla con la que implica la cuota.

Decision de diseno importante: REGRESION LOGISTICA, no un arbol ni una red.
Con 238 filas un modelo flexible memoriza el ruido y da +40% en train y -20%
en test. Ademas tiene que ser INTERPRETABLE: el usuario va a ver por que se
apuesta, y eso exige coeficientes, no 300 arboles.

Regla de oro del modulo: si el backtest no sale positivo, no se usa. Por eso
backtest.py es bloqueante y no opcional.

Features (todas disponibles SIN llamadas de red, para que el dataset se pueda
reconstruir en cualquier momento y de forma reproducible):
    - cuota:      la implicita en el precio es la referencia contra la que se
                  compara la probabilidad estimada
    - mercado:    categorica. El historico muestra que algunos mercados son
                  estructuralmente mejores que otros (ver REPORTE): 'Ambos
                  equipos marcan' acierta 43% y 'Over de tarjetas' 18%, frente
                  a 'Corners equipo A o B' con 89%. Esa dispersion es senal
                  real, no azar.
    - dia de la semana /Sport no: se omite a proposito. Con 238 filas, meter
      mas columnas empeora el modelo; el sobreajuste es el riesgo real aqui.
"""

import json
import os

_NUM = ["cuota"]
_CAT = ["market_key"]
_TODAS = _NUM + _CAT


def normaliza_mercado(texto: str) -> str:
    """Clave canonica del mercado. Texto libre -> etiqueta estable."""
    t = (texto or "").lower().strip()
    if not t:
        return "otro"
    # corners primero: 'tiros de esquina' y 'corner' son el mismo mercado
    if "esquina" in t or "corner" in t:
        if "primera mitad" in t or "1" in t[:6]:
            return "corners_1t"
        if "equipo" in t or "equipo a" in t or "equipo b" in t:
            return "corners_equipo"
        return "corners_total"
    if "tarjeta" in t or "card" in t:
        return "tarjetas"
    if "faltas" in t or "foul" in t:
        return "faltas"
    if "btts" in t or "ambos equipos marcan" in t:
        return "btts"
    if "doble oportunidad" in t:
        return "doble_oportunidad"
    if "1x2" in t:
        return "1x2"
    if "multigol" in t:
        return "multigoles"
    if "handicap" in t:
        return "handicap"
    if "goles" in t or "gol" in t:
        if "mitad" in t:
            return "goles_1t"
        return "goles"
    return "otro"


def features_de_fila(fila: dict) -> dict:
    """Convierte una fila de ai_picks en el vector del modelo."""
    try:
        cuota = float(fila.get("odds") or 0)
    except (TypeError, ValueError):
        cuota = 0.0
    return {
        "cuota": cuota,
        "market_key": normaliza_mercado(fila.get("market")),
    }


def_X = None  # marcador para el import (evita linters de orden)


def construir_dataset(filas: list[dict]) -> tuple:
    """(X, y, fechas) a partir de filas de ai_picks.

    Descarta lo que no sirve: resultado distinto de ACIERTO/FALLO, cuota <= 1.
    """
    X, y, fechas = [], [], []
    for f in filas:
        res = (f.get("result") or "").upper()
        if res not in ("ACIERTO", "FALLO"):
            continue
        feats = features_de_fila(f)
        if feats["cuota"] <= 1.0:
            continue
        X.append(feats)
        y.append(1 if res == "ACIERTO" else 0)
        fechas.append(str(f.get("event_date") or ""))
    return X, y, fechas


# === Modelo ================================================================

def a_df(X):
    """Lista de dicts -> DataFrame. ColumnTransformer con nombres de columna
    exige un DataFrame: pasar dicts directamente revienta con
    'estimator input should be a 2D array'."""
    import pandas as pd
    return pd.DataFrame(X, columns=_TODAS)


def _pipeline():
    """ColumnTransformer + LogisticRegression. Sin fuga de datos: el scaler se
    ajusta DENTRO de train en cada split (si se ajustara antes, el backtest
    estaria viendo el futuro)."""
    from sklearn.compose import ColumnTransformer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    return Pipeline([
        ("prep", ColumnTransformer([
            ("num", StandardScaler(), _NUM),
            ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), _CAT),
        ])),
        ("clf", LogisticRegression(class_weight="balanced", C=1.0,
                                   max_iter=1000, random_state=0)),
    ])


def entrenar(X, y):
    """Entrena y devuelve el pipeline. None si no hay datos suficientes."""
    if len(y) < 20 or len(set(y)) < 2:
        return None
    p = _pipeline()
    p.fit(a_df(X), y)
    return p


def predecir(pipeline, feats) -> float:
    """Probabilidad estimada de acierto. 0.5 si no se puede (p. ej. mercado
    que el modelo nunca vio)."""
    if pipeline is None:
        return 0.5
    try:
        return float(pipeline.predict_proba(a_df([feats]))[0][1])
    except Exception:
        return 0.5


# === Reporte de mercado (para leer los coeficientes) =======================

def reporte_mercados(filas: list[dict]) -> list:
    """Acierto y ROI por mercado, para inspeccion manual."""
    from collections import defaultdict
    g = defaultdict(list)
    for f in filas:
        if (f.get("result") or "").upper() not in ("ACIERTO", "FALLO"):
            continue
        try:
            o = float(f.get("odds") or 0)
        except (TypeError, ValueError):
            continue
        if o <= 1:
            continue
        g[normaliza_mercado(f.get("market"))].append((o, f["result"].upper()))
    filas_out = []
    for k, v in g.items():
        n = len(v)
        ac = sum(1 for _, r in v if r == "ACIERTO") / n * 100
        roi = sum((o - 1) if r == "ACIERTO" else -1 for o, r in v) / n * 100
        filas_out.append({"market_key": k, "n": n, "acierto": ac, "roi": roi})
    filas_out.sort(key=lambda x: -x["roi"])
    return filas_out


# === Backtest walk-forward (BLOQUEANTE) ===================================

def walk_forward(X, y, fechas, n_splits: int = 5, umbral_ev: float = 0.05):
    """Validacion temporal: por split, entrena con lo ANTERIOR y predice lo
    POSTERIOR. Nunca aleatorio.

    Es la unica forma honesta con estos datos: un split aleatorio deja que el
    modelo vea el futuro y devuelve un ROI que luego no se reproduce.

    En cada split se aplica la MISMA regla dedecision que se usaria en vivo:
    apostar solo si el valor esperado (prob*(cuota-1) - (1-prob)) supera
    umbral_ev. Devuelve tambien el ROI de apostar SIEMPRE, que es la referencia
    contra la que hay que comparar.
    """
    from sklearn.metrics import roc_auc_score

    orden = sorted(range(len(y)), key=lambda i: (fechas[i], i))
    Xs = [X[i] for i in orden]
    ys = [y[i] for i in orden]
    fs = [fechas[i] for i in orden]

    n = len(ys)
    if n < 40:
        return {"error": f"muy pocos datos ({n})"}

    # cortes temporales en cuantiles para que los splits tengan tamano parejo
    cortes = [int(n * i / n_splits) for i in range(1, n_splits)]
    splits = []
    ini = 0
    for corte in cortes + [n]:
        train_idx = list(range(0, corte))
        test_idx = list(range(corte, min(corte + (n // n_splits), n)))
        if len(test_idx) < 3 or len(train_idx) < 20:
            if corte != cortes[-1] or not splits:
                ini = corte
                continue
        ini = corte
        splits.append((train_idx, test_idx))
    if not splits:
        return {"error": "no se pudieron formar splits"}

    apostadas = 0
    unidades = 0.0
    baseline_unidades = 0.0
    baseline_n = 0
    aciertos = 0
    detalle = []

    for train_idx, test_idx in splits:
        Xtr = [Xs[i] for i in train_idx]
        ytr = [ys[i] for i in train_idx]
        Xte = [Xs[i] for i in test_idx]
        yte = [ys[i] for i in test_idx]
        feats_te = [Xs[i] for i in test_idx]

        pipe = _pipeline()
        pipe.fit(a_df(Xtr), ytr)

        s_uni = 0.0
        s_ap = 0
        for fte, yte_i in zip(feats_te, yte):
            o = float(fte.get("cuota") or 1)
            # referencia: apostar siempre
            s_uni += (o - 1) if yte_i == 1 else -1
            # regla del modelo
            prob = predecir(pipe, fte)
            ev = prob * (o - 1) - (1 - prob)
            if ev >= umbral_ev:
                s_uni_ap = (o - 1) if yte_i == 1 else -1
                s_uni += 0  # ya sumado arriba
                unidades += s_uni_ap
                apostadas += 1
                if yte_i == 1:
                    aciertos += 1
        unidades_split = s_uni  # baseline del split
        baseline_unidades += s_uni
        baseline_n += len(yte)
        detalle.append({
            "train": len(train_idx), "test": len(test_idx),
            "apostadas": s_ap,
            "baseline_unidades": round(s_uni, 2),
        })

    roi = (unidades / apostadas * 100) if apostadas else 0.0
    acierto = (aciertos / apostadas * 100) if apostadas else 0.0
    base_roi = (baseline_unidades / baseline_n * 100) if baseline_n else 0.0

    try:
        pipe_full = _pipeline()
        pipe_full.fit(a_df(X), y)
        auc = float(roc_auc_score(y, pipe_full.predict_proba(a_df(X))[:, 1]))
    except Exception:
        auc = None

    splits_positivos = sum(1 for d in detalle if d["baseline_unidades"] > 0)
    return {
        "n_datos": n,
        "n_splits": len(splits),
        "apostadas": apostadas,
        "acierto": round(acierto, 1),
        "unidades": round(unidades, 2),
        "roi_modelo": round(roi, 1),
        "roi_baseline_siempre": round(base_roi, 1),
        "splits_positivos": splits_positivos,
        "auc_in_sample": round(auc, 3) if auc else None,
        "detalle": detalle,
        "VEREDICTO": "POSITIVO" if roi > 0 and roi > base_roi else "NO APTO",
    }
