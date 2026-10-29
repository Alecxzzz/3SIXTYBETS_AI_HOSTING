# -*- coding: utf-8 -*-
"""
Aplica la migracion de "recuperacion de contrasena por email" (IDEMPOTENTE):

  - tabla `email_tokens` (tokens de un solo uso, hasheados)
  - columnas `users.email` y `users.email_verified_at`
  - indice unico `users_email_uq` (email)

La app lo aplica sola al arrancar (db.init_db), pero este script permite
adelantarlo sin desplegar. NO toca ninguna fila existente: solo añade estructura,
asi que es seguro sobre la BD de produccion.

Uso:
    python migrar_recuperacion_email.py
"""

import db


def main() -> int:
    print("Aplicando migracion de recuperacion por email...")

    db.run_query(
        """
        create table if not exists email_tokens (
            id varchar(64) primary key,
            user_id varchar(64) not null,
            purpose varchar(20) not null,
            token_hash varchar(128) not null,
            expires_at datetime not null,
            used_at datetime null,
            created_at datetime not null,
            index email_tokens_user_idx (user_id, purpose),
            index email_tokens_hash_idx (token_hash),
            foreign key (user_id) references users(id) on delete cascade
        )
        """
    )
    print("  [OK] tabla email_tokens")

    for column, tipo in (("email", "varchar(255) null"),
                         ("email_verified_at", "datetime null")):
        existe = db.run_query(
            """
            select count(*) as cnt from information_schema.columns
            where table_schema = database() and table_name = 'users' and column_name = %s
            """,
            (column,),
            fetchone=True,
        )
        if existe and not existe.get("cnt"):
            db.run_query(f"alter table users add column {column} {tipo}")
            print(f"  [OK] users.{column} añadida")
        else:
            print(f"  [--] users.{column} ya existia")

    indice = db.run_query(
        """
        select count(*) as cnt from information_schema.statistics
        where table_schema = database() and table_name = 'users'
          and index_name = 'users_email_uq'
        """,
        fetchone=True,
    )
    if indice and not indice.get("cnt"):
        db.run_query("alter table users add unique key users_email_uq (email)")
        print("  [OK] indice unico users_email_uq")
    else:
        print("  [--] indice users_email_uq ya existia")

    columnas = db.run_query(
        """
        select column_name as nombre from information_schema.columns
        where table_schema = database() and table_name = 'users'
        """,
        fetchone=False,
    ) or []
    print("\nColumnas de users:", ", ".join(sorted(c["nombre"] for c in columnas)))
    print("Migracion lista.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
