#!/usr/bin/env python3
"""
Dockerman Deadline System
Sistema de deadlines com contagem regressiva contínua e barra de urgência.
Backend: Python puro + SQLite + API REST
Inclui: histórico de alterações,
comentários, membros com cores/avatares, subtarefas, tempo estimado/real
e exportação/importação em JSON.
"""

import json
import os
import sqlite3
import sys
import urllib.parse
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

# Configurações
PORT = int(os.environ.get("PORT", 9999))
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DB_PATH = DATA_DIR / "deadlines.db"
PUBLIC_DIR = Path(__file__).parent / "public"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# Sem login: ações são registradas com este usuário padrão
DEFAULT_USER = "admin"


# ============ Banco ============

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
                members TEXT DEFAULT '[]',
                tags TEXT DEFAULT '[]',
                priority TEXT DEFAULT 'medium',
                estimated_hours REAL DEFAULT 0,
                actual_hours REAL DEFAULT 0
            )
        """)
        # Migração segura: adiciona as colunas se ainda não existirem
        cols = [r[1] for r in conn.execute("PRAGMA table_info(deadlines)").fetchall()]
        migrations = {
            "steps": "TEXT DEFAULT '[]'",
            "members": "TEXT DEFAULT '[]'",
            "tags": "TEXT DEFAULT '[]'",
            "priority": "TEXT DEFAULT 'medium'",
            "estimated_hours": "REAL DEFAULT 0",
            "actual_hours": "REAL DEFAULT 0",
        }
        for col, ddl in migrations.items():
            if col not in cols:
                conn.execute(f"ALTER TABLE deadlines ADD COLUMN {col} {ddl}")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deadline_id INTEGER,
                user TEXT NOT NULL,
                action TEXT NOT NULL,
                detail TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deadline_id INTEGER NOT NULL,
                user TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
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


def add_history(conn, deadline_id, user, action, detail=""):
    conn.execute(
        "INSERT INTO history (deadline_id, user, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
        (deadline_id, user, action, detail, now_iso()),
    )


# ============ Normalizadores ============

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


def normalize_tasks(raw):
    """Valida e normaliza a lista de subtarefas: [{text, done}, ...]"""
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
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        result.append({"text": text, "done": bool(item.get("done"))})
    return result


MEMBER_COLORS = [
    "#06b6d4", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
    "#ec4899", "#14b8a6", "#f97316", "#3b82f6", "#84cc16",
]


def normalize_color(raw, name):
    if isinstance(raw, str) and len(raw) in (4, 7) and raw.startswith("#"):
        return raw
    h = sum(ord(c) for c in (name or "membro"))
    return MEMBER_COLORS[h % len(MEMBER_COLORS)]


def normalize_members(raw):
    """Valida e normaliza a lista de membros: [{name, steps, tasks, color}, ...]"""
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
        tasks = normalize_tasks(item.get("tasks"))
        color = normalize_color(item.get("color"), name)
        result.append({"name": name, "steps": steps, "tasks": tasks, "color": color})
    return result


def normalize_tags(raw):
    """Valida e normaliza a lista de tags (strings únicas)."""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raw = [t.strip() for t in raw.split(",")]
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        tag = str(item or "").strip()
        if tag and tag not in result:
            result.append(tag)
    return result


def normalize_priority(raw):
    """Valida a prioridade: low | medium | high (default medium)."""
    return raw if raw in ("low", "medium", "high") else "medium"


def normalize_hours(raw):
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, v), 1)


def row_to_dict(row):
    steps = normalize_steps(row["steps"]) if "steps" in row.keys() else []
    members = normalize_members(row["members"]) if "members" in row.keys() else []
    tags = normalize_tags(row["tags"]) if "tags" in row.keys() else []
    priority = normalize_priority(row["priority"]) if "priority" in row.keys() else "medium"
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
        "tags": tags,
        "priority": priority,
        "estimated_hours": row["estimated_hours"] or 0 if "estimated_hours" in row.keys() else 0,
        "actual_hours": row["actual_hours"] or 0 if "actual_hours" in row.keys() else 0,
    }


# ============ Export / Import ============

def export_data():
    with get_db() as conn:
        deadlines = [row_to_dict(r) for r in conn.execute("SELECT * FROM deadlines").fetchall()]
        comments = [dict(r) for r in conn.execute("SELECT * FROM comments ORDER BY id").fetchall()]
        history = [dict(r) for r in conn.execute("SELECT * FROM history ORDER BY id").fetchall()]
    return {"exported_at": now_iso(), "deadlines": deadlines, "comments": comments, "history": history}


def import_data(payload):
    deadlines = payload.get("deadlines") if isinstance(payload.get("deadlines"), list) else []
    comments = payload.get("comments") if isinstance(payload.get("comments"), list) else []
    history = payload.get("history") if isinstance(payload.get("history"), list) else []
    with get_db() as conn:
        conn.execute("DELETE FROM deadlines")
        conn.execute("DELETE FROM comments")
        conn.execute("DELETE FROM history")
        for d in deadlines:
            if not isinstance(d, dict):
                continue
            try:
                did = int(d.get("id"))
            except (TypeError, ValueError):
                continue
            title = (d.get("title") or "").strip()
            due_at = d.get("due_at")
            if not title or not due_at:
                continue
            conn.execute(
                """
                INSERT INTO deadlines (id, title, description, due_at, created_at, updated_at, finished_at,
                                       steps, members, tags, priority, estimated_hours, actual_hours)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    did,
                    title,
                    (d.get("description") or "").strip(),
                    due_at,
                    d.get("created_at") or now_iso(),
                    d.get("updated_at") or now_iso(),
                    d.get("finished_at"),
                    json.dumps(normalize_steps(d.get("steps")), ensure_ascii=False),
                    json.dumps(normalize_members(d.get("members")), ensure_ascii=False),
                    json.dumps(normalize_tags(d.get("tags")), ensure_ascii=False),
                    normalize_priority(d.get("priority")),
                    normalize_hours(d.get("estimated_hours")),
                    normalize_hours(d.get("actual_hours")),
                ),
            )
        for c in comments:
            if not isinstance(c, dict):
                continue
            try:
                cid = int(c.get("id"))
                cdead = int(c.get("deadline_id"))
            except (TypeError, ValueError):
                continue
            text = (c.get("text") or "").strip()
            if not text:
                continue
            conn.execute(
                "INSERT INTO comments (id, deadline_id, user, text, created_at) VALUES (?, ?, ?, ?, ?)",
                (cid, cdead, (c.get("user") or "?"), text, c.get("created_at") or now_iso()),
            )
        for h in history:
            if not isinstance(h, dict):
                continue
            try:
                hid = int(h.get("id"))
            except (TypeError, ValueError):
                continue
            try:
                hdead = int(h.get("deadline_id")) if h.get("deadline_id") is not None else None
            except (TypeError, ValueError):
                hdead = None
            conn.execute(
                "INSERT INTO history (id, deadline_id, user, action, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (hid, hdead, (h.get("user") or "?"), (h.get("action") or "atualizou"),
                 (h.get("detail") or ""), h.get("created_at") or now_iso()),
            )
        add_history(conn, None, DEFAULT_USER, "importou",
                    f"{len(deadlines)} deadlines, {len(comments)} comentários, {len(history)} registros")
        conn.commit()
    return {"ok": True, "deadlines": len(deadlines), "comments": len(comments)}


# ============ HTTP ============

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
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        return json.loads(text)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Backup completo
        if path == "/api/export":
            self._send_json(export_data())
            return

        # Lista todos os deadlines
        if path == "/api/deadlines":
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT * FROM deadlines ORDER BY finished_at IS NOT NULL, due_at ASC"
                ).fetchall()
            self._send_json([row_to_dict(r) for r in rows])
            return

        # Histórico / comentários de um deadline: /api/deadlines/123/history
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "deadlines" and parts[2].isdigit():
            deadline_id = int(parts[2])
            if parts[3] == "history":
                with get_db() as conn:
                    rows = conn.execute(
                        "SELECT * FROM history WHERE deadline_id = ? ORDER BY id ASC", (deadline_id,)
                    ).fetchall()
                self._send_json([dict(r) for r in rows])
                return
            if parts[3] == "comments":
                with get_db() as conn:
                    rows = conn.execute(
                        "SELECT * FROM comments WHERE deadline_id = ? ORDER BY id ASC", (deadline_id,)
                    ).fetchall()
                self._send_json([dict(r) for r in rows])
                return

        # Serve o frontend
        if path == "/" or path == "/index.html":
            self._serve_file(PUBLIC_DIR / "index.html", "text/html; charset=utf-8")
            return

        # Arquivos estáticos
        if path.startswith("/"):
            file_path = PUBLIC_DIR / path.lstrip("/")
            if file_path.is_file() and PUBLIC_DIR in file_path.resolve().parents:
                content_type = self._guess_type(file_path)
                self._serve_file(file_path, content_type)
                return

        self._send_error("Não encontrado", 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        # Importar backup
        if path == "/api/import":
            try:
                payload = self._read_json()
            except json.JSONDecodeError:
                self._send_error("JSON inválido")
                return
            self._send_json(import_data(payload))
            return

        # Criar novo deadline
        if path == "/api/deadlines":
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
            tags = normalize_tags(data.get("tags"))
            priority = normalize_priority(data.get("priority"))
            estimated_hours = normalize_hours(data.get("estimated_hours"))
            actual_hours = normalize_hours(data.get("actual_hours"))
            now = now_iso()
            steps_json = json.dumps(steps, ensure_ascii=False)
            members_json = json.dumps(members, ensure_ascii=False)
            tags_json = json.dumps(tags, ensure_ascii=False)

            with get_db() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO deadlines (title, description, due_at, created_at, updated_at, finished_at,
                                           steps, members, tags, priority, estimated_hours, actual_hours)
                    VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
                    """,
                    (title, description, due_at, now, now, steps_json, members_json,
                     tags_json, priority, estimated_hours, actual_hours),
                )
                new_id = cur.lastrowid
                add_history(conn, new_id, DEFAULT_USER, "criou", title)
                conn.commit()
                row = conn.execute("SELECT * FROM deadlines WHERE id = ?", (new_id,)).fetchone()

            self._send_json(row_to_dict(row), 201)
            return

        # Comentário em um deadline: /api/deadlines/123/comments
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "deadlines" and parts[2].isdigit() and parts[3] == "comments":
            try:
                data = self._read_json()
            except json.JSONDecodeError:
                self._send_error("JSON inválido")
                return
            text = (data.get("text") or "").strip()
            if not text:
                self._send_error("Comentário vazio")
                return
            deadline_id = int(parts[2])
            with get_db() as conn:
                exists = conn.execute("SELECT id FROM deadlines WHERE id = ?", (deadline_id,)).fetchone()
                if not exists:
                    self._send_error("Deadline não encontrado", 404)
                    return
                cur = conn.execute(
                    "INSERT INTO comments (deadline_id, user, text, created_at) VALUES (?, ?, ?, ?)",
                    (deadline_id, DEFAULT_USER, text, now_iso()),
                )
                add_history(conn, deadline_id, DEFAULT_USER, "comentou", text[:80])
                conn.commit()
                row = conn.execute("SELECT * FROM comments WHERE id = ?", (cur.lastrowid,)).fetchone()
            self._send_json(dict(row), 201)
            return

        self._send_error("Não encontrado", 404)

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

            if "steps" in data:
                steps = normalize_steps(data["steps"])
            else:
                steps = normalize_steps(row["steps"]) if "steps" in row.keys() else []
            steps_json = json.dumps(steps, ensure_ascii=False)

            if "members" in data:
                members = normalize_members(data["members"])
            else:
                members = normalize_members(row["members"]) if "members" in row.keys() else []
            members_json = json.dumps(members, ensure_ascii=False)

            if "tags" in data:
                tags = normalize_tags(data["tags"])
            else:
                tags = normalize_tags(row["tags"]) if "tags" in row.keys() else []
            tags_json = json.dumps(tags, ensure_ascii=False)

            if "priority" in data:
                priority = normalize_priority(data["priority"])
            else:
                priority = normalize_priority(row["priority"]) if "priority" in row.keys() else "medium"

            estimated_hours = normalize_hours(data.get("estimated_hours", row["estimated_hours"] if "estimated_hours" in row.keys() else 0))
            actual_hours = normalize_hours(data.get("actual_hours", row["actual_hours"] if "actual_hours" in row.keys() else 0))

            finished_transition = None
            if data.get("finished") is True and not finished_at:
                finished_at = now
                finished_transition = "finalizou"
            if data.get("finished") is False:
                if finished_at:
                    finished_transition = "reabriu"
                finished_at = None

            if not title:
                self._send_error("Título é obrigatório")
                return

            # Histórico: quais campos mudaram
            changes = []
            old_tags = normalize_tags(row["tags"]) if "tags" in row.keys() else []
            old_members = normalize_members(row["members"]) if "members" in row.keys() else []
            if title != (row["title"] or "").strip():
                changes.append("título")
            if description != (row["description"] or ""):
                changes.append("descrição")
            if due_at != row["due_at"]:
                changes.append("data")
            if tags != old_tags:
                changes.append("tags")
            if priority != normalize_priority(row["priority"] if "priority" in row.keys() else "medium"):
                changes.append("prioridade")
            if estimated_hours != normalize_hours(row["estimated_hours"] if "estimated_hours" in row.keys() else 0):
                changes.append("tempo estimado")
            if actual_hours != normalize_hours(row["actual_hours"] if "actual_hours" in row.keys() else 0):
                changes.append("tempo real")
            if members != old_members:
                changes.append("membros/subtarefas")

            conn.execute(
                """
                UPDATE deadlines
                SET title = ?, description = ?, due_at = ?, updated_at = ?, finished_at = ?,
                    steps = ?, members = ?, tags = ?, priority = ?, estimated_hours = ?, actual_hours = ?
                WHERE id = ?
                """,
                (title, description, due_at, now, finished_at, steps_json, members_json,
                 tags_json, priority, estimated_hours, actual_hours, deadline_id),
            )
            add_history(conn, deadline_id, DEFAULT_USER, finished_transition or "atualizou",
                        ", ".join(changes) if changes else "sem mudanças")
            conn.commit()
            updated = conn.execute("SELECT * FROM deadlines WHERE id = ?", (deadline_id,)).fetchone()

        self._send_json(row_to_dict(updated))

    def do_DELETE(self):
        parts = self.path.strip("/").split("/")

        # Excluir comentário: /api/comments/123
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "comments":
            try:
                comment_id = int(parts[2])
            except ValueError:
                self._send_error("ID inválido")
                return
            with get_db() as conn:
                row = conn.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
                if not row:
                    self._send_error("Comentário não encontrado", 404)
                    return
                conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
                add_history(conn, row["deadline_id"], DEFAULT_USER, "removeu comentário", row["text"][:80])
                conn.commit()
            self._send_json({"ok": True, "deleted": comment_id})
            return

        # Excluir deadline: /api/deadlines/123
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "deadlines":
            try:
                deadline_id = int(parts[2])
            except ValueError:
                self._send_error("ID inválido")
                return
            with get_db() as conn:
                row = conn.execute("SELECT * FROM deadlines WHERE id = ?", (deadline_id,)).fetchone()
                if not row:
                    self._send_error("Deadline não encontrado", 404)
                    return
                conn.execute("DELETE FROM deadlines WHERE id = ?", (deadline_id,))
                conn.execute("DELETE FROM comments WHERE deadline_id = ?", (deadline_id,))
                add_history(conn, deadline_id, DEFAULT_USER, "excluiu", row["title"])
                conn.commit()
            self._send_json({"ok": True, "deleted": deadline_id})
            return

        self._send_error("Não encontrado", 404)

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