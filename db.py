import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.getenv("DATABASE_PATH", "threesixtybets.db")


def get_connection():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.execute(
            """
            create table if not exists users (
                id text primary key,
                username text not null,
                email text not null unique,
                password_hash text not null,
                created_at text not null
            )
            """
        )
        conn.execute(
            """
            create table if not exists sessions (
                token text primary key,
                user_id text not null references users(id) on delete cascade,
                created_at text not null
            )
            """
        )
        conn.execute(
            """
            create table if not exists chat_messages (
                id text primary key,
                user_id text not null references users(id) on delete cascade,
                role text not null check (role in ('user', 'ai')),
                text text not null,
                created_at text not null
            )
            """
        )
        conn.execute(
            """
            create index if not exists chat_messages_user_created_idx
            on chat_messages (user_id, created_at)
            """
        )


def now_iso():
    return datetime.now(timezone.utc).isoformat()


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
        "email": row["email"],
    }


def create_user(username, email, password):
    user_id = secrets.token_urlsafe(16)

    with get_connection() as conn:
        conn.execute(
            """
            insert into users (id, username, email, password_hash, created_at)
            values (?, ?, ?, ?, ?)
            """,
            (user_id, username, email, hash_password(password), now_iso()),
        )
        row = conn.execute("select * from users where id = ?", (user_id,)).fetchone()

    return public_user(row)


def get_user_by_email(email):
    with get_connection() as conn:
        return conn.execute("select * from users where email = ?", (email,)).fetchone()


def create_session(user_id):
    token = secrets.token_urlsafe(32)

    with get_connection() as conn:
        conn.execute(
            "insert into sessions (token, user_id, created_at) values (?, ?, ?)",
            (token, user_id, now_iso()),
        )

    return token


def delete_session(token):
    with get_connection() as conn:
        conn.execute("delete from sessions where token = ?", (token,))


def get_user_by_token(token):
    with get_connection() as conn:
        return conn.execute(
            """
            select users.*
            from sessions
            join users on users.id = sessions.user_id
            where sessions.token = ?
            """,
            (token,),
        ).fetchone()


def list_messages(user_id):
    with get_connection() as conn:
        rows = conn.execute(
            """
            select id, role, text, created_at
            from chat_messages
            where user_id = ?
            order by created_at asc
            """,
            (user_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def create_message(user_id, role, text):
    message_id = secrets.token_urlsafe(16)

    with get_connection() as conn:
        conn.execute(
            """
            insert into chat_messages (id, user_id, role, text, created_at)
            values (?, ?, ?, ?, ?)
            """,
            (message_id, user_id, role, text, now_iso()),
        )
        row = conn.execute(
            "select id, role, text, created_at from chat_messages where id = ?",
            (message_id,),
        ).fetchone()

    return dict(row)
