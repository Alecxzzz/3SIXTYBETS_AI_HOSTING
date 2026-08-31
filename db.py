import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

import pymysql
import pymysql.cursors
import pymysql.err
from dotenv import load_dotenv


load_dotenv()


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_mysql_config():
    # Soporta tanto una URL completa (MYSQL_URL, formato de Aiven
    # "mysql://user:pass@host:port/db?ssl-mode=REQUIRED") como variables
    # sueltas (MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD,
    # MYSQL_DATABASE), que es lo que ya tienes configurado en Northflank.
    mysql_url = os.getenv("MYSQL_URL", "").strip() or os.getenv("DATABASE_URL", "").strip()

    if mysql_url:
        parsed = urlparse(mysql_url)
        query_params = parse_qs(parsed.query)
        ssl_mode = query_params.get("ssl-mode", query_params.get("sslmode", ["REQUIRED"]))[0]

        return {
            "host": parsed.hostname,
            "port": parsed.port or 3306,
            "user": parsed.username,
            "password": parsed.password,
            "database": parsed.path.lstrip("/"),
            "ssl_disabled": ssl_mode.upper() == "DISABLED",
        }

    ssl_disabled_env = os.getenv("MYSQL_SSL_DISABLED", "false").strip().lower()

    return {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "database": os.getenv("MYSQL_DATABASE", "sixtybets"),
        "ssl_disabled": ssl_disabled_env in ("1", "true", "yes"),
    }


def get_connection(autocommit=True):
    config = get_mysql_config()

    connect_kwargs = dict(
        host=config["host"],
        port=config["port"],
        user=config["user"],
        password=config["password"],
        database=config["database"],
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=20,
        autocommit=autocommit,
    )

    # Aiven exige TLS. pymysql habilita cifrado pasando un dict "ssl";
    # no verificamos el certificado contra una CA local para simplificar
    # (equivalente a sslmode=require de Postgres, no full-verify).
    if not config["ssl_disabled"]:
        connect_kwargs["ssl"] = {"ssl": {}}

    return pymysql.connect(**connect_kwargs)


def run_query(query, params=None, fetchone=False):
    for _ in range(5):
        conn = None
        cur = None
        try:
            conn = get_connection(autocommit=True)
            cur = conn.cursor()
            cur.execute(query, params or ())

            stripped = query.strip().upper()
            if stripped.startswith("SELECT") or stripped.startswith("SHOW") or stripped.startswith("WITH"):
                result = cur.fetchone() if fetchone else cur.fetchall()
                return result

            return True
        except pymysql.MySQLError as error:
            print("MySQL error:", error)
            time.sleep(3)
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

    return None


def init_db():
    statements = [
        """
        create table if not exists users (
            id varchar(64) primary key,
            username varchar(80) not null unique,
            password_hash varchar(255) not null,
            role varchar(20) not null default 'user',
            credits int not null default 0,
            access_expires_at datetime not null,
            created_at datetime not null
        )
        """,
        """
        create table if not exists sessions (
            token varchar(128) primary key,
            user_id varchar(64) not null,
            created_at datetime not null,
            foreign key (user_id) references users(id) on delete cascade
        )
        """,
        """
        create table if not exists redeem_keys (
            id varchar(64) primary key,
            code varchar(32) not null unique,
            duration_days int not null,
            key_expires_at datetime null,
            created_by varchar(64) null,
            claimed_by varchar(64) null,
            claimed_at datetime null,
            created_at datetime not null,
            foreign key (created_by) references users(id) on delete set null,
            foreign key (claimed_by) references users(id) on delete set null
        )
        """,
        """
        create table if not exists chat_messages (
            id varchar(64) primary key,
            user_id varchar(64) not null,
            role varchar(20) not null,
            `text` text not null,
            created_at datetime not null,
            index chat_messages_user_created_idx (user_id, created_at),
            foreign key (user_id) references users(id) on delete cascade
        )
        """,
        """
        create table if not exists credit_transactions (
            id varchar(64) primary key,
            user_id varchar(64) not null,
            amount int not null,
            status varchar(30) not null,
            provider varchar(40) not null,
            reference varchar(160) null,
            created_at datetime not null,
            foreign key (user_id) references users(id) on delete cascade
        )
        """,
        """
        create table if not exists pagadito_orders (
            id varchar(64) primary key,
            user_id varchar(64) not null,
            plan_code varchar(40) not null,
            amount decimal(10, 2) not null,
            currency varchar(3) not null default 'USD',
            ern varchar(64) not null unique,
            token_trans varchar(128) null,
            status varchar(20) not null default 'pending',
            reference varchar(160) null,
            created_at datetime not null,
            updated_at datetime null,
            foreign key (user_id) references users(id) on delete cascade
        )
        """,
    ]

    for statement in statements:
        run_query(statement)

    ensure_admin_user()


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120000,
    ).hex()
    return f"{salt}:{digest}"


def verify_password(password, stored_hash):
    try:
        salt, expected = stored_hash.split(":", 1)
    except ValueError:
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120000,
    ).hex()
    return hmac.compare_digest(digest, expected)


def public_user(row):
    return {
        "id": row["id"],
        "name": row["username"],
        "username": row["username"],
        "role": row["role"],
        "credits": row["credits"],
        "access_expires_at": row["access_expires_at"].isoformat()
        if row.get("access_expires_at")
        else None,
    }


def generate_redeem_code():
    part_a = secrets.token_hex(2).upper()
    part_b = secrets.token_hex(2).upper()
    return f"SIXTYBETS-{part_a}-{part_b}"


def create_redeem_key(duration_days, created_by=None, key_expires_at=None):
    while True:
        key_id = secrets.token_urlsafe(16)
        code = generate_redeem_code()
        ok = run_query(
            """
            insert into redeem_keys
            (id, code, duration_days, key_expires_at, created_by, created_at)
            values (%s, %s, %s, %s, %s, %s)
            """,
            (key_id, code, duration_days, key_expires_at, created_by, now_utc()),
        )
        if ok:
            return run_query("select * from redeem_keys where id = %s", (key_id,), fetchone=True)


def claim_redeem_key(code, user_id, cur):
    clean_code = code.strip().upper()
    cur.execute("select * from redeem_keys where code = %s for update", (clean_code,))
    key = cur.fetchone()

    if not key:
        raise ValueError("Codigo de canjeo invalido.")

    if key["claimed_by"]:
        raise ValueError("Esta key ya fue reclamada.")

    if key["key_expires_at"] and key["key_expires_at"] < now_utc():
        raise ValueError("Esta key ya expiro.")

    access_expires_at = now_utc() + timedelta(days=key["duration_days"])
    cur.execute(
        """
        update redeem_keys
        set claimed_by = %s, claimed_at = %s
        where id = %s and claimed_by is null
        """,
        (user_id, now_utc(), key["id"]),
    )

    if cur.rowcount != 1:
        raise ValueError("Esta key ya fue reclamada.")

    return access_expires_at


def redeem_key_for_user(user_id, code):
    conn = None
    cur = None

    try:
        conn = get_connection(autocommit=False)
        cur = conn.cursor()
        access_expires_at = claim_redeem_key(code, user_id, cur)

        cur.execute("select access_expires_at from users where id = %s", (user_id,))
        user = cur.fetchone()
        current_expires_at = user["access_expires_at"] if user else now_utc()
        base_date = max(current_expires_at, now_utc())
        days_added = max(
            int(((access_expires_at - now_utc()).total_seconds() + 86399) // 86400),
            1,
        )
        next_expires_at = base_date + timedelta(days=days_added)

        cur.execute(
            "update users set access_expires_at = %s where id = %s",
            (next_expires_at, user_id),
        )
        conn.commit()

        return {
            "days_added": days_added,
            "access_expires_at": next_expires_at.isoformat(),
        }
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def create_user(username, password, redeem_code):
    user_id = secrets.token_urlsafe(16)
    username = username.strip()
    conn = None
    cur = None

    try:
        conn = get_connection(autocommit=False)
        cur = conn.cursor()
        cur.execute(
            """
            insert into users
            (id, username, password_hash, access_expires_at, created_at)
            values (%s, %s, %s, %s, %s)
            """,
            (user_id, username, hash_password(password), now_utc(), now_utc()),
        )

        access_expires_at = claim_redeem_key(redeem_code, user_id, cur)
        cur.execute(
            "update users set access_expires_at = %s where id = %s",
            (access_expires_at, user_id),
        )
        cur.execute("select * from users where id = %s", (user_id,))
        user = public_user(cur.fetchone())
        conn.commit()
        return user
    except pymysql.err.IntegrityError:
        if conn:
            conn.rollback()
        raise ValueError("Ya existe una cuenta con ese usuario.") from None
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def ensure_admin_user():
    username = os.getenv("ADMIN_USERNAME", "").strip()
    password = os.getenv("ADMIN_PASSWORD", "").strip()

    if not username or not password:
        return

    # El admin/propietario tiene acceso ILIMITADO: nunca expira.
    # Usamos una fecha muy lejana (anio 9999) en vez de NULL.
    access_expires_at = datetime(9999, 12, 31, 23, 59, 59)
    existing = get_user_by_username(username)
    if existing:
        run_query(
            """
            update users
            set password_hash = %s, role = 'admin', access_expires_at = %s
            where id = %s
            """,
            (hash_password(password), access_expires_at, existing["id"]),
        )
        return

    run_query(
        """
        insert into users
        (id, username, password_hash, role, access_expires_at, created_at)
        values (%s, %s, %s, 'admin', %s, %s)
        """,
        (
            secrets.token_urlsafe(16),
            username,
            hash_password(password),
            access_expires_at,
            now_utc(),
        ),
    )


def get_user_by_username(username):
    return run_query("select * from users where username = %s", (username,), fetchone=True)


def health_status():
    db_ok = bool(run_query("select 1 as ok", fetchone=True))
    admin_username = os.getenv("ADMIN_USERNAME", "").strip()
    admin_password = os.getenv("ADMIN_PASSWORD", "").strip()
    admin = get_user_by_username(admin_username) if admin_username else None

    return {
        "db_ok": db_ok,
        "admin_configured": bool(admin_username and admin_password),
        "admin_username": admin_username or None,
        "admin_exists": bool(admin),
        "admin_role": admin["role"] if admin else None,
    }


def create_session(user_id):
    token = secrets.token_urlsafe(32)
    run_query(
        "insert into sessions (token, user_id, created_at) values (%s, %s, %s)",
        (token, user_id, now_utc()),
    )
    return token


def delete_session(token):
    run_query("delete from sessions where token = %s", (token,))


def get_user_by_token(token):
    return run_query(
        """
        select users.*
        from sessions
        join users on users.id = sessions.user_id
        where sessions.token = %s
        """,
        (token,),
        fetchone=True,
    )


def list_messages(user_id):
    rows = run_query(
        """
        select id, role, `text`, created_at
        from chat_messages
        where user_id = %s
        and created_at >= %s
        order by created_at asc
        """,
        (user_id, now_utc() - timedelta(hours=24)),
    ) or []

    return [
        {
            **row,
            "created_at": row["created_at"].isoformat()
            if row.get("created_at")
            else None,
        }
        for row in rows
    ]


def create_message(user_id, role, text):
    message_id = secrets.token_urlsafe(16)
    run_query(
        """
        insert into chat_messages (id, user_id, role, `text`, created_at)
        values (%s, %s, %s, %s, %s)
        """,
        (message_id, user_id, role, text, now_utc()),
    )
    row = run_query(
        "select id, role, `text`, created_at from chat_messages where id = %s",
        (message_id,),
        fetchone=True,
    )
    row["created_at"] = row["created_at"].isoformat()
    return row


def list_redeem_keys():
    rows = run_query(
        """
        select rk.code, rk.duration_days, rk.key_expires_at, rk.claimed_by,
               rk.claimed_at, rk.created_at, u.username
        from redeem_keys rk
        left join users u on u.id = rk.claimed_by
        order by rk.created_at desc
        limit 100
        """
    ) or []

    return [
        {
            **row,
            "status": "claimed"
            if row.get("claimed_by")
            else "expired"
            if row.get("key_expires_at") and row["key_expires_at"] < now_utc()
            else "available",
            "claimed_by_username": row.get("username"),
            "key_expires_at": row["key_expires_at"].isoformat()
            if row.get("key_expires_at")
            else None,
            "claimed_at": row["claimed_at"].isoformat()
            if row.get("claimed_at")
            else None,
            "created_at": row["created_at"].isoformat()
            if row.get("created_at")
            else None,
        }
        for row in rows
    ]


def delete_unclaimed_key(code: str) -> bool:
    """
    Borra una key SOLO si no ha sido reclamada.
    Devuelve True si se borró, False si no existe o ya fue reclamada.
    """
    code = (code or "").strip().upper()
    row = run_query(
        "select claimed_by from redeem_keys where code = %s",
        (code,),
        fetchone=True,
    )
    if not row or row.get("claimed_by"):
        return False
    return bool(
        run_query("delete from redeem_keys where code = %s", (code,))
    )


# ==============================
# PAGADITO: ordenes y suscripciones
# ==============================

def create_pagadito_order(user_id, plan_code, amount, currency="USD"):
    """Crea una orden pendiente con ERN unico. Devuelve la fila creada."""
    order_id = secrets.token_urlsafe(16)
    ern = f"SIXTYB-{secrets.token_hex(12)}"  # ID unico de la orden para Pagadito
    ok = run_query(
        """
        insert into pagadito_orders
        (id, user_id, plan_code, amount, currency, ern, status, created_at)
        values (%s, %s, %s, %s, %s, %s, 'pending', %s)
        """,
        (order_id, user_id, plan_code, amount, currency, ern, now_utc()),
    )
    if not ok:
        raise RuntimeError("No se pudo registrar la orden de pago.")
    return run_query(
        "select * from pagadito_orders where id = %s", (order_id,), fetchone=True
    )


def get_pagadito_order_by_ern(ern):
    return run_query(
        "select * from pagadito_orders where ern = %s", (ern,), fetchone=True
    )


def attach_pagadito_token(ern, token_trans):
    """Guarda el token de transaccion devuelto por exec_trans."""
    return bool(
        run_query(
            "update pagadito_orders set token_trans = %s where ern = %s",
            (token_trans, ern),
        )
    )


def mark_pagadito_order_status(ern, status, reference=None):
    """Actualiza el estado de la orden: pending / completed / failed."""
    return bool(
        run_query(
            """
            update pagadito_orders
            set status = %s, reference = %s, updated_at = %s
            where ern = %s
            """,
            (status, reference, now_utc(), ern),
        )
    )


def activate_subscription(ern, plan_days):
    """
    Activa/renueva la suscripcion del usuario dueño de la orden.
    Extiende access_expires_at desde la fecha mas lejana entre "ahora"
    y la expiracion actual (igual que el canje de keys). Marca la orden
    como completada. Idempotente: si ya estaba completada no vuelve a
    extender los dias.

    Devuelve dict {"ok", "already_processed", "access_expires_at", "username"}.
    """
    order = get_pagadito_order_by_ern(ern)
    if not order:
        raise ValueError("Orden de pago no encontrada.")

    conn = None
    cur = None
    try:
        conn = get_connection(autocommit=False)
        cur = conn.cursor()

        cur.execute("select * from pagadito_orders where ern = %s for update", (ern,))
        order = cur.fetchone()
        if order["status"] == "completed":
            # Ya procesada (reintento de Pagadito / doble click): no duplicar dias.
            cur.execute("select username, access_expires_at from users where id = %s", (order["user_id"],))
            user = cur.fetchone()
            conn.commit()
            return {
                "ok": True,
                "already_processed": True,
                "access_expires_at": user["access_expires_at"].isoformat() if user else None,
                "username": user["username"] if user else None,
            }

        cur.execute("select username, access_expires_at from users where id = %s", (order["user_id"],))
        user = cur.fetchone()
        if not user:
            raise ValueError("Usuario de la orden no encontrado.")

        current_expires_at = user["access_expires_at"]
        base_date = max(current_expires_at, now_utc())
        next_expires_at = base_date + timedelta(days=plan_days)

        cur.execute(
            "update users set access_expires_at = %s where id = %s",
            (next_expires_at, order["user_id"]),
        )
        cur.execute(
            """
            update pagadito_orders
            set status = 'completed', updated_at = %s
            where ern = %s and status != 'completed'
            """,
            (now_utc(), ern),
        )
        conn.commit()

        return {
            "ok": True,
            "already_processed": False,
            "access_expires_at": next_expires_at.isoformat(),
            "username": user["username"],
        }
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
