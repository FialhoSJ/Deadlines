#!/usr/bin/env python3
"""
Dockerman Deadline System
Sistema de deadlines com contagem regressiva contínua e barra de urgência.
Backend: Python puro + SQLite + API REST
"""

import json
import os
import sqlite3
import urllib.parse
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# Configurações
PORT = int(os.environ.get("PORT", 9999))
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DB_PATH = DATA_DIR / "deadlines.db"
PUBLIC_DIR = Path(__file__).parent / "public"

DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS deadlines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                due_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT,
                steps TEXT DEFAULT '[]',
                members TEXT DEFAULT '[]'
            )
        """)
        # Migração segura: adiciona as colunas se ainda não existirem
        cols = [r[1] for r in conn.execute("PRAGMA table_info(deadlines)").fetchall()]
        if "steps" not in cols:
            conn.execute("ALTER TABLE deadlines ADD COLUMN steps TEXT DEFAULT '[]'")
        if "members" not in cols:
            conn.execute("ALTER TABLE deadlines ADD COLUMN members TEXT DEFAULT '[]'")
        # Backfill: registros antigos sem membros viram um membro (nome = description)
        rows = conn.execute(
            "SELECT id, description, steps FROM deadlines WHERE members IS NULL OR members = '' OR members = '[]'"
        ).fetchall()
        for r in rows:
            if r["description"] or (r["steps"] and r["steps"] != "[]"):
                steps = normalize_steps(r["steps"])
                members = json.dumps(
                    [{"name": r["description"] or "Membro", "steps": steps}], ensure_ascii=False
                )
                conn.execute("UPDATE deadlines SET members = ? WHERE id = ?", (members, r["id"]))
        conn.commit()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def normalize_steps(raw):
    """Valida e normaliza a lista de índices das etapas marcadas (0-4)."""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        try:
            item = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= item <= 4 and item not in result:
            result.append(item)
    return result


def normalize_members(raw):
    """Valida e normaliza a lista de membros: [{name, steps}, ...]"""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        steps = normalize_steps(item.get("steps"))
        result.append({"name": name, "steps": steps})
    return result


def row_to_dict(row):
    steps = normalize_steps(row["steps"]) if "steps" in row.keys() else []
    members = normalize_members(row["members"]) if "members" in row.keys() else []
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"] or "",
        "due_at": row["due_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "finished_at": row["finished_at"],
        "steps": steps,
        "members": members,
    }


class DeadlineHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}")

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, message, status=400):
        self._send_json({"error": message}, status)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Lista todos os deadlines
        if path == "/api/deadlines":
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT * FROM deadlines ORDER BY finished_at IS NOT NULL, due_at ASC"
                ).fetchall()
            self._send_json([row_to_dict(r) for r in rows])
            return

        # Serve o frontend
        if path == "/" or path == "/index.html":
            self._serve_file(PUBLIC_DIR / "index.html", "text/html; charset=utf-8")
            return

        # Arquivos estáticos (caso precise no futuro)
        if path.startswith("/"):
            file_path = PUBLIC_DIR / path.lstrip("/")
            if file_path.is_file() and PUBLIC_DIR in file_path.resolve().parents:
                content_type = self._guess_type(file_path)
                self._serve_file(file_path, content_type)
                return

        self._send_error("Não encontrado", 404)

    def do_POST(self):
        # Criar novo deadline
        if self.path != "/api/deadlines":
            self._send_error("Não encontrado", 404)
            return

        try:
            data = self._read_json()
        except json.JSONDecodeError:
            self._send_error("JSON inválido")
            return

        title = (data.get("title") or "").strip()
        if not title:
            self._send_error("Título é obrigatório")
            return

        due_at = data.get("due_at")
        if not due_at:
            self._send_error("Data de deadline é obrigatória")
            return

        description = (data.get("description") or "").strip()
        steps = normalize_steps(data.get("steps"))
        if "members" in data:
            members = normalize_members(data["members"])
        else:
            members = [{"name": description, "steps": steps}] if description else []
        now = now_iso()
        steps_json = json.dumps(steps, ensure_ascii=False)
        members_json = json.dumps(members, ensure_ascii=False)

        with get_db() as conn:
            cur = conn.execute(
                """
                INSERT INTO deadlines (title, description, due_at, created_at, updated_at, finished_at, steps, members)
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (title, description, due_at, now, now, steps_json, members_json),
            )
            new_id = cur.lastrowid
            conn.commit()
            row = conn.execute("SELECT * FROM deadlines WHERE id = ?", (new_id,)).fetchone()

        self._send_json(row_to_dict(row), 201)

    def do_PUT(self):
        # Atualizar deadline existente: /api/deadlines/123
        parts = self.path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "api" or parts[1] != "deadlines":
            self._send_error("Não encontrado", 404)
            return

        try:
            deadline_id = int(parts[2])
        except ValueError:
            self._send_error("ID inválido")
            return

        try:
            data = self._read_json()
        except json.JSONDecodeError:
            self._send_error("JSON inválido")
            return

        with get_db() as conn:
            row = conn.execute("SELECT * FROM deadlines WHERE id = ?", (deadline_id,)).fetchone()
            if not row:
                self._send_error("Deadline não encontrado", 404)
                return

            title = data.get("title", row["title"]).strip()
            description = data.get("description", row["description"] or "")
            due_at = data.get("due_at", row["due_at"])
            finished_at = row["finished_at"]
            now = now_iso()

            # Steps: se enviadas, substitui; senão mantém
            if "steps" in data:
                steps = normalize_steps(data["steps"])
            else:
                steps = normalize_steps(row["steps"]) if "steps" in row.keys() else []
            steps_json = json.dumps(steps, ensure_ascii=False)

            # Members: se enviados, substitui; senão mantém
            if "members" in data:
                members = normalize_members(data["members"])
            else:
                members = normalize_members(row["members"]) if "members" in row.keys() else []
            members_json = json.dumps(members, ensure_ascii=False)

            # Marcar como finalizado
            if data.get("finished") is True and not finished_at:
                finished_at = now
            # Reabrir
            if data.get("finished") is False:
                finished_at = None

            if not title:
                self._send_error("Título é obrigatório")
                return

            conn.execute(
                """
                UPDATE deadlines
                SET title = ?, description = ?, due_at = ?, updated_at = ?, finished_at = ?, steps = ?, members = ?
                WHERE id = ?
                """,
                (title, description, due_at, now, finished_at, steps_json, members_json, deadline_id),
            )
            conn.commit()
            updated = conn.execute("SELECT * FROM deadlines WHERE id = ?", (deadline_id,)).fetchone()

        self._send_json(row_to_dict(updated))

    def do_DELETE(self):
        # Excluir deadline: /api/deadlines/123
        parts = self.path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "api" or parts[1] != "deadlines":
            self._send_error("Não encontrado", 404)
            return

        try:
            deadline_id = int(parts[2])
        except ValueError:
            self._send_error("ID inválido")
            return

        with get_db() as conn:
            cur = conn.execute("DELETE FROM deadlines WHERE id = ?", (deadline_id,))
            conn.commit()
            if cur.rowcount == 0:
                self._send_error("Deadline não encontrado", 404)
                return

        self._send_json({"ok": True, "deleted": deadline_id})

    def _serve_file(self, path: Path, content_type: str):
        try:
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            self._send_error("Erro ao servir arquivo", 500)

    def _guess_type(self, path: Path) -> str:
        ext = path.suffix.lower()
        return {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }.get(ext, "application/octet-stream")


def main():
    init_db()
    print("=" * 60)
    print("  🐳 Dockerman Deadline System")
    print("  Sistema de deadlines com contagem regressiva e urgência")
    print("=" * 60)
    print(f"  Banco: {DB_PATH}")
    print(f"  Servindo em: https://192.168.1.80:{PORT}")
    print("  Pressione Ctrl+C para parar")
    print("=" * 60)

    server = HTTPServer(("", PORT), DeadlineHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Dockerman] Encerrando servidor...")
        server.server_close()


if __name__ == "__main__":
    main()
