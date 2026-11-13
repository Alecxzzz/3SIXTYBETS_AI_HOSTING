# -*- coding: utf-8 -*-
"""Crea las TRES tablas del motor Elo + Monte Carlo en la BD configurada.

Por que un script aparte y no `db.init_db()`
--------------------------------------------
`init_db()` ejecuta TODAS las sentencias (`create table if not exists` de users,
sessions, ai_picks, player_props...). Lanzarlo a mano contra una base que ya
esta en produccion funciona, pero toca 30 tablas para crear 3. Este script
ejecuta solo las tres nuevas, es idempotente (`if not exists`) y por tanto se
puede lanzar tantas veces como haga falta.

Uso:
    python crear_tablas_elo.py
"""

import sys

import db

TABLAS = [
    """
    create table if not exists elo_ratings (
        sport varchar(20) not null,
        entidad_id varchar(40) not null,
        rating decimal(10, 3) not null default 1500,
        partidos int not null default 0,
        fecha_ultimo datetime null,
        pf decimal(8, 3) null,
        pa decimal(8, 3) null,
        total decimal(8, 3) null,
        n_forma int not null default 0,
        actualizado datetime not null,
        primary key (sport, entidad_id)
    )
    """,
    """
    create table if not exists elo_calibracion (
        id int auto_increment primary key,
        sport varchar(20) not null,
        a decimal(10, 4) null,
        b decimal(10, 4) null,
        c decimal(10, 4) null,
        t0 decimal(10, 4) null,
        t1 decimal(10, 4) null,
        sigma_margen decimal(10, 4) null,
        sigma_total decimal(10, 4) null,
        sigma_equipo decimal(10, 4) null,
        rho decimal(10, 4) null,
        rho_equipos decimal(10, 4) null,
        p_ot decimal(10, 4) null,
        total_medio decimal(10, 4) null,
        n_partidos int null,
        creado datetime not null,
        index elo_calibracion_sport_idx (sport, id)
    )
    """,
    """
    create table if not exists ai_picks_prob (
        id varchar(64) primary key,
        pick_id varchar(64) null,
        event_id varchar(64) not null,
        sport varchar(20) not null,
        market varchar(200) null,
        seleccion varchar(200) null,
        linea decimal(10, 3) null,
        prob decimal(8, 5) null,
        prob_low decimal(8, 5) null,
        prob_high decimal(8, 5) null,
        edge decimal(8, 5) null,
        cuota decimal(10, 4) null,
        mu_local decimal(10, 3) null,
        mu_visita decimal(10, 3) null,
        elo_local decimal(10, 3) null,
        elo_visita decimal(10, 3) null,
        n_sims int null,
        modelo_version varchar(40) null,
        descartado tinyint not null default 0,
        motivo varchar(60) null,
        creado datetime not null,
        index ai_picks_prob_event_idx (event_id),
        index ai_picks_prob_sport_idx (sport, creado)
    )
    """,
]


def main() -> int:
    fallos = 0
    for sentencia in TABLAS:
        nombre = sentencia.split("exists", 1)[1].split("(", 1)[0].strip()
        ok = db.run_query(sentencia)
        print(f"  {'OK  ' if ok else 'FALLO'} {nombre}")
        if not ok:
            fallos += 1
    if fallos:
        print(f"\n{fallos} tabla(s) no se pudieron crear.")
        return 1
    print("\nTablas del motor listas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
