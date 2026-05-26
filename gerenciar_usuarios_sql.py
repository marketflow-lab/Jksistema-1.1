import argparse
import json
import os
import sqlite3
from datetime import datetime

import bcrypt

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "info", "auth_users.db")

PERMISSION_KEYS = [
    "analise_promo",
    "renovacao_fixa",
    "vendas",
    "estoque",
    "integracao",
    "etiquetas",
    "full",
    "favoritos",
    "anuncios_ml",
    "medias_compras",
    "cadastro",
    "impostos",
    "configuracoes",
    "importacoes",
]

ALIASES = {
    "promo": "analise_promo",
    "analise": "analise_promo",
    "renovacao": "renovacao_fixa",
    "integracoes": "integracao",
    "anuncios": "anuncios_ml",
    "ml": "anuncios_ml",
    "medias": "medias_compras",
    "compras": "medias_compras",
    "config": "configuracoes",
    "importacao": "importacoes",
}


def connect_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = connect_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usuarios_auth (
                username TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                name TEXT NOT NULL,
                client_id TEXT NOT NULL,
                permissions_json TEXT NOT NULL DEFAULT '{}',
                active INTEGER NOT NULL DEFAULT 1,
                valid_until TEXT,
                machine_id TEXT,
                source TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()



def hash_password(password: str) -> str:
    if str(password).startswith("$2"):
        return str(password)
    return bcrypt.hashpw(str(password).encode("utf-8"), bcrypt.gensalt()).decode("utf-8")



def normalize_permissions(raw: str | None):
    perms = {key: False for key in PERMISSION_KEYS}
    if not raw:
        return perms

    tokens = [t.strip().lower() for t in str(raw).split(",") if t.strip()]
    for token in tokens:
        key = ALIASES.get(token, token)
        if key == "full":
            perms["full"] = True
            for k in perms:
                perms[k] = True
            continue
        if key in perms:
            perms[key] = True
    return perms



def get_user(username: str):
    conn = connect_db()
    try:
        row = conn.execute(
            "SELECT username, name, client_id, permissions_json, active, valid_until, machine_id, source, created_at, updated_at FROM usuarios_auth WHERE username = ?",
            (username.strip().lower(),),
        ).fetchone()
        return row
    finally:
        conn.close()



def list_users():
    conn = connect_db()
    try:
        rows = conn.execute(
            "SELECT username, name, client_id, active, valid_until, source FROM usuarios_auth ORDER BY username"
        ).fetchall()
        if not rows:
            print("Nenhum usuário cadastrado no SQL.")
            return
        print("\nUsuários cadastrados:\n")
        for row in rows:
            status = "ATIVO" if int(row["active"] or 0) == 1 else "INATIVO"
            validade = row["valid_until"] or "sem validade"
            print(f"- {row['username']} | {row['name']} | cliente={row['client_id']} | {status} | validade={validade} | origem={row['source'] or 'sql'}")
    finally:
        conn.close()



def upsert_user(username: str, password: str, name: str, client_id: str, permissions: str, active: bool = True,
                valid_until: str | None = None, machine_id: str | None = None, source: str = "sql-admin"):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    username = username.strip().lower()
    payload_permissions = normalize_permissions(permissions)

    conn = connect_db()
    try:
        conn.execute(
            """
            INSERT INTO usuarios_auth (
                username, password, name, client_id, permissions_json,
                active, valid_until, machine_id, source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                password=excluded.password,
                name=excluded.name,
                client_id=excluded.client_id,
                permissions_json=excluded.permissions_json,
                active=excluded.active,
                valid_until=excluded.valid_until,
                machine_id=excluded.machine_id,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (
                username,
                hash_password(password),
                name.strip() or username,
                client_id.strip(),
                json.dumps(payload_permissions, ensure_ascii=False),
                1 if active else 0,
                valid_until or None,
                machine_id or None,
                source,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    print(f"Usuário '{username}' salvo com sucesso.")
    print(f"Permissões: {', '.join([k for k, v in payload_permissions.items() if v]) or 'nenhuma'}")



def update_permissions(username: str, permissions: str):
    row = get_user(username)
    if not row:
        raise SystemExit(f"Usuário '{username}' não encontrado.")

    conn = connect_db()
    try:
        conn.execute(
            "UPDATE usuarios_auth SET permissions_json = ?, updated_at = ? WHERE username = ?",
            (
                json.dumps(normalize_permissions(permissions), ensure_ascii=False),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                username.strip().lower(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    print(f"Permissões do usuário '{username}' atualizadas.")



def update_password(username: str, password: str):
    row = get_user(username)
    if not row:
        raise SystemExit(f"Usuário '{username}' não encontrado.")

    conn = connect_db()
    try:
        conn.execute(
            "UPDATE usuarios_auth SET password = ?, updated_at = ? WHERE username = ?",
            (
                hash_password(password),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                username.strip().lower(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    print(f"Senha do usuário '{username}' atualizada.")



def set_active(username: str, active: bool):
    row = get_user(username)
    if not row:
        raise SystemExit(f"Usuário '{username}' não encontrado.")

    conn = connect_db()
    try:
        conn.execute(
            "UPDATE usuarios_auth SET active = ?, updated_at = ? WHERE username = ?",
            (1 if active else 0, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), username.strip().lower()),
        )
        conn.commit()
    finally:
        conn.close()

    print(f"Usuário '{username}' {'ativado' if active else 'desativado'} com sucesso.")



def remove_user(username: str):
    row = get_user(username)
    if not row:
        raise SystemExit(f"Usuário '{username}' não encontrado.")

    conn = connect_db()
    try:
        conn.execute("DELETE FROM usuarios_auth WHERE username = ?", (username.strip().lower(),))
        conn.commit()
    finally:
        conn.close()

    print(f"Usuário '{username}' removido com sucesso.")



def show_user(username: str):
    row = get_user(username)
    if not row:
        raise SystemExit(f"Usuário '{username}' não encontrado.")

    perms = json.loads(row["permissions_json"] or "{}")
    print(json.dumps({
        "username": row["username"],
        "name": row["name"],
        "client_id": row["client_id"],
        "active": bool(int(row["active"] or 0)),
        "valid_until": row["valid_until"],
        "machine_id": row["machine_id"],
        "source": row["source"],
        "permissions": perms,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }, indent=2, ensure_ascii=False))



def build_parser():
    parser = argparse.ArgumentParser(
        description="Gerencia usuários e permissões do login SQL do sistema."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("listar", help="Lista todos os usuários do banco")

    add = sub.add_parser("adicionar", help="Cria ou atualiza um usuário")
    add.add_argument("username")
    add.add_argument("password")
    add.add_argument("client_id")
    add.add_argument("--nome", default="")
    add.add_argument("--permissoes", default="")
    add.add_argument("--validade", default="")
    add.add_argument("--maquina", default="")
    add.add_argument("--inativo", action="store_true")

    pwd = sub.add_parser("senha", help="Altera apenas a senha")
    pwd.add_argument("username")
    pwd.add_argument("password")

    perm = sub.add_parser("permissoes", help="Altera as permissões do usuário")
    perm.add_argument("username")
    perm.add_argument("permissoes")

    info = sub.add_parser("ver", help="Mostra os dados completos do usuário")
    info.add_argument("username")

    ativar = sub.add_parser("ativar", help="Ativa um usuário")
    ativar.add_argument("username")

    desativar = sub.add_parser("desativar", help="Desativa um usuário")
    desativar.add_argument("username")

    remover = sub.add_parser("remover", help="Remove um usuário do banco")
    remover.add_argument("username")

    return parser



def main():
    init_db()
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "listar":
        list_users()
    elif args.command == "adicionar":
        upsert_user(
            username=args.username,
            password=args.password,
            name=args.nome or args.username,
            client_id=args.client_id,
            permissions=args.permissoes,
            active=not args.inativo,
            valid_until=args.validade or None,
            machine_id=args.maquina or None,
        )
    elif args.command == "senha":
        update_password(args.username, args.password)
    elif args.command == "permissoes":
        update_permissions(args.username, args.permissoes)
    elif args.command == "ver":
        show_user(args.username)
    elif args.command == "ativar":
        set_active(args.username, True)
    elif args.command == "desativar":
        set_active(args.username, False)
    elif args.command == "remover":
        remove_user(args.username)


if __name__ == "__main__":
    main()
