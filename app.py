#!/usr/bin/env python3
"""
Dockerman Deadline System
Sistema de deadlines com contagem regressiva contínua, barra de urgência
e tags com porcentagem de completude (cor vermelho → verde).
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
PORT = int(os.environ.get("PORT", 8000))
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
                tags TEXT DEFAULT '[]'
            )
        """)
        # Migração segura: adiciona coluna tags se ainda não existir
        cols = [r[1] for r in conn.execute("PRAGMA table_info(deadlines)").fetchall()]
        if "tags" not in cols:
            conn.execute("ALTER TABLE deadlines ADD COLUMN tags TEXT DEFAULT '[]'")
        conn.commit()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def normalize_tags(raw):
    """Valida e normaliza lista de tags: [{name, percent}, ...]"""
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
        try:
            percent = float(item.get("percent", 0))
        except (TypeError, ValueError):
            percent = 0.0
        percent = max(0.0, min(100.0, percent))
        result.append({"name": name, "percent": round(percent, 1)})
    return result


def row_to_dict(row):
    tags = normalize_tags(row["tags"] if "tags" in row.keys() else "[]")
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"] or "",
        "due_at": row["due_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "finished_at": row["finished_at"],
        "tags": tags,
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

        if path == "/api/deadlines":
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT * FROM deadlines ORDER BY finished_at IS NOT NULL, due_at ASC"
                ).fetchall()
            self._send_json([row_to_dict(r) for r in rows])
            return

        if path == "/" or path == "/index.html":
            self._serve_file(PUBLIC_DIR / "index.html", "text/html; charset=utf-8")
            return

        # Servir arquivos estáticos de /public
        if path.startswith("/"):
            file_path = PUBLIC_DIR / path.lstrip("/")
            if file_path.is_file() and PUBLIC_DIR in file_path.resolve().parents:
                content_type = self._guess_type(file_path)
                self._serve_file(file_path, content_type)
                return

        self._send_error("Não encontrado", 404)

    def do_POST(self):
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
        tags = normalize_tags(data.get("tags"))
        now = now_iso()
        tags_json = json.dumps(tags, ensure_ascii=False)

        with get_db() as conn:
            cur = conn.execute(
                """
                INSERT INTO deadlines (title, description, due_at, created_at, updated_at, finished_at, tags)
                VALUES (?, ?, ?, ?, ?, NULL, ?)
                """,
                (title, description, due_at, now, now, tags_json),
            )
            new_id = cur.lastrowid
            conn.commit()
            row = conn.execute("SELECT * FROM deadlines WHERE id = ?", (new_id,)).fetchone()

        self._send_json(row_to_dict(row), 201)

    def do_PUT(self):
        # /api/deadlines/123
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

            # Tags: se enviadas, substitui; senão mantém
            if "tags" in data:
                tags = normalize_tags(data["tags"])
            else:
                tags = normalize_tags(row["tags"] if "tags" in row.keys() else "[]")
            tags_json = json.dumps(tags, ensure_ascii=False)

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
                SET title = ?, description = ?, due_at = ?, updated_at = ?, finished_at = ?, tags = ?
                WHERE id = ?
                """,
                (title, description, due_at, now, finished_at, tags_json, deadline_id),
            )
            conn.commit()
            updated = conn.execute("SELECT * FROM deadlines WHERE id = ?", (deadline_id,)).fetchone()

        self._send_json(row_to_dict(updated))

    def do_DELETE(self):
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
    print("  Sistema de deadlines com contagem regressiva, urgência e tags")
    print("=" * 60)
    print(f"  Banco: {DB_PATH}")
    print(f"  Servindo em: http://0.0.0.0:{PORT}")
    print("  Pressione Ctrl+C para parar")
    print("=" * 60)

    server = HTTPServer(("0.0.0.0", PORT), DeadlineHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Dockerman] Encerrando servidor...")
        server.server_close()


if __name__ == "__main__":
    main()
