"""HPLC instrument availability dashboard.

A small, dependency-free web server (Python standard library + SQLite) that
tracks which HPLC systems are in use, who is running them, and when each run
will finish. Run it on any lab PC and open the dashboard from any browser on
the network:

    python server.py --host 0.0.0.0 --port 8080
"""

import argparse
import json
import re
import sqlite3
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_DB = Path(__file__).parent / "hplc.db"

MAX_RUN_MINUTES = 7 * 24 * 60  # one week
INITIALS_RE = re.compile(r"^[A-Z]{1,4}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS systems (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL UNIQUE,
    model          TEXT NOT NULL DEFAULT '',
    location       TEXT NOT NULL DEFAULT '',
    out_of_service INTEGER NOT NULL DEFAULT 0,
    notes          TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id   INTEGER NOT NULL REFERENCES systems(id) ON DELETE CASCADE,
    analyst     TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    started_at  REAL NOT NULL,
    ends_at     REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS runs_active ON runs(system_id, finished_at);
"""


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Store:
    """All database access. A single lock serialises writers."""

    def __init__(self, path, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    # -- systems ---------------------------------------------------------

    def _system(self, system_id):
        row = self.db.execute("SELECT * FROM systems WHERE id = ?", (system_id,)).fetchone()
        if row is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "System not found")
        return row

    def _active_run(self, system_id):
        return self.db.execute(
            "SELECT * FROM runs WHERE system_id = ? AND finished_at IS NULL",
            (system_id,),
        ).fetchone()

    def add_system(self, data):
        name = _text(data, "name", required=True, max_len=60)
        with self.lock, self.db:
            try:
                cur = self.db.execute(
                    "INSERT INTO systems (name, model, location, notes) VALUES (?, ?, ?, ?)",
                    (name, _text(data, "model"), _text(data, "location"), _text(data, "notes", max_len=500)),
                )
            except sqlite3.IntegrityError:
                raise ApiError(HTTPStatus.CONFLICT, f"A system named '{name}' already exists")
            return {"id": cur.lastrowid}

    def update_system(self, system_id, data):
        with self.lock, self.db:
            self._system(system_id)
            fields = {}
            if "name" in data:
                fields["name"] = _text(data, "name", required=True, max_len=60)
            for key in ("model", "location"):
                if key in data:
                    fields[key] = _text(data, key)
            if "notes" in data:
                fields["notes"] = _text(data, "notes", max_len=500)
            if "out_of_service" in data:
                if self._active_run(system_id) and data["out_of_service"]:
                    raise ApiError(HTTPStatus.CONFLICT, "Release the current run before taking the system out of service")
                fields["out_of_service"] = 1 if data["out_of_service"] else 0
            if fields:
                sets = ", ".join(f"{k} = ?" for k in fields)
                try:
                    self.db.execute(f"UPDATE systems SET {sets} WHERE id = ?", (*fields.values(), system_id))
                except sqlite3.IntegrityError:
                    raise ApiError(HTTPStatus.CONFLICT, "Another system already has that name")
            return {"ok": True}

    def delete_system(self, system_id):
        with self.lock, self.db:
            self._system(system_id)
            self.db.execute("DELETE FROM systems WHERE id = ?", (system_id,))
            return {"ok": True}

    # -- runs ------------------------------------------------------------

    def start_run(self, system_id, data):
        analyst = str(data.get("analyst", "")).strip().upper()
        if not INITIALS_RE.match(analyst):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Analyst initials must be 1-4 letters")
        minutes = _minutes(data, "duration_minutes")
        with self.lock, self.db:
            system = self._system(system_id)
            if system["out_of_service"]:
                raise ApiError(HTTPStatus.CONFLICT, "System is out of service")
            if self._active_run(system_id):
                raise ApiError(HTTPStatus.CONFLICT, "System is already in use")
            now = self.clock()
            cur = self.db.execute(
                "INSERT INTO runs (system_id, analyst, description, started_at, ends_at) VALUES (?, ?, ?, ?, ?)",
                (system_id, analyst, _text(data, "description", max_len=200), now, now + minutes * 60),
            )
            return {"id": cur.lastrowid}

    def _open_run(self, run_id):
        run = self.db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "Run not found")
        if run["finished_at"] is not None:
            raise ApiError(HTTPStatus.CONFLICT, "Run has already been released")
        return run

    def extend_run(self, run_id, data):
        minutes = _minutes(data, "minutes")
        with self.lock, self.db:
            run = self._open_run(run_id)
            # Extending an overdue run counts from now, not from the old end time.
            base = max(run["ends_at"], self.clock())
            self.db.execute("UPDATE runs SET ends_at = ? WHERE id = ?", (base + minutes * 60, run_id))
            return {"ok": True}

    def finish_run(self, run_id):
        with self.lock, self.db:
            self._open_run(run_id)
            self.db.execute("UPDATE runs SET finished_at = ? WHERE id = ?", (self.clock(), run_id))
            return {"ok": True}

    # -- views -----------------------------------------------------------

    def state(self):
        with self.lock:
            return self._state()

    def _state(self):
        now = self.clock()
        systems = []
        for s in self.db.execute("SELECT * FROM systems ORDER BY name COLLATE NOCASE"):
            run = self._active_run(s["id"])
            if s["out_of_service"]:
                status = "maintenance"
            elif run is None:
                status = "free"
            elif run["ends_at"] > now:
                status = "running"
            else:
                status = "done"  # timer elapsed, waiting for analyst to release it
            systems.append({
                "id": s["id"],
                "name": s["name"],
                "model": s["model"],
                "location": s["location"],
                "notes": s["notes"],
                "status": status,
                "run": dict(run) if run else None,
            })
        return {"now": now, "systems": systems}

    def history(self, days):
        with self.lock:
            return self._history(days)

    def _history(self, days):
        now = self.clock()
        since = now - days * 86400
        rows = self.db.execute(
            """SELECT r.*, s.name AS system_name FROM runs r JOIN systems s ON s.id = r.system_id
               WHERE COALESCE(r.finished_at, ?) >= ? ORDER BY r.started_at DESC""",
            (now, since),
        ).fetchall()
        busy = {}
        for r in rows:
            # Instrument time = from start until the timer ended or it was released, whichever came first.
            end = min(r["ends_at"], r["finished_at"] if r["finished_at"] is not None else now)
            busy[r["system_id"]] = busy.get(r["system_id"], 0) + max(0.0, end - max(r["started_at"], since))
        window = days * 86400
        utilization = [
            {
                "system_id": s["id"],
                "name": s["name"],
                "busy_hours": round(busy.get(s["id"], 0) / 3600, 1),
                "percent": round(100 * busy.get(s["id"], 0) / window, 1),
                "runs": sum(1 for r in rows if r["system_id"] == s["id"]),
            }
            for s in self.db.execute("SELECT id, name FROM systems ORDER BY name COLLATE NOCASE")
        ]
        return {"days": days, "utilization": utilization, "runs": [dict(r) for r in rows[:200]]}


def _text(data, key, required=False, max_len=100):
    value = str(data.get(key) or "").strip()
    if required and not value:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"'{key}' is required")
    if len(value) > max_len:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"'{key}' must be at most {max_len} characters")
    return value


def _minutes(data, key):
    try:
        minutes = float(data.get(key))
    except (TypeError, ValueError):
        raise ApiError(HTTPStatus.BAD_REQUEST, f"'{key}' must be a number of minutes")
    if not 0 < minutes <= MAX_RUN_MINUTES:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"'{key}' must be between 0 and {MAX_RUN_MINUTES}")
    return minutes


ROUTES = [
    ("GET", r"/api/state", lambda st, m, b, q: st.state()),
    ("GET", r"/api/history", lambda st, m, b, q: st.history(_days(q))),
    ("POST", r"/api/systems", lambda st, m, b, q: st.add_system(b)),
    ("PATCH", r"/api/systems/(\d+)", lambda st, m, b, q: st.update_system(int(m[1]), b)),
    ("DELETE", r"/api/systems/(\d+)", lambda st, m, b, q: st.delete_system(int(m[1]))),
    ("POST", r"/api/systems/(\d+)/runs", lambda st, m, b, q: st.start_run(int(m[1]), b)),
    ("POST", r"/api/runs/(\d+)/extend", lambda st, m, b, q: st.extend_run(int(m[1]), b)),
    ("POST", r"/api/runs/(\d+)/finish", lambda st, m, b, q: st.finish_run(int(m[1]))),
]


def _days(query):
    params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
    try:
        return min(max(int(params.get("days", 7)), 1), 365)
    except ValueError:
        raise ApiError(HTTPStatus.BAD_REQUEST, "'days' must be an integer")


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # keep the console quiet; it's a wall display

        def _send(self, status, body, content_type="application/json"):
            payload = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _dispatch(self, method):
            path, _, query = self.path.partition("?")
            if method == "GET" and not path.startswith("/api/"):
                return self._static(path)
            try:
                body = {}
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ApiError(HTTPStatus.BAD_REQUEST, "Body must be a JSON object")
                for verb, pattern, fn in ROUTES:
                    match = re.fullmatch(pattern, path)
                    if verb == method and match:
                        return self._send(HTTPStatus.OK, fn(store, match, body, query))
                raise ApiError(HTTPStatus.NOT_FOUND, "Not found")
            except ApiError as e:
                self._send(e.status, {"error": e.message})
            except json.JSONDecodeError:
                self._send(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON"})

        def _static(self, path):
            if path == "/":
                path = "/index.html"
            file = (STATIC_DIR / path.lstrip("/")).resolve()
            if STATIC_DIR.resolve() not in file.parents or not file.is_file():
                return self._send(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            types = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css"}
            self._send(HTTPStatus.OK, file.read_bytes(), types.get(file.suffix, "application/octet-stream"))

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def do_DELETE(self):
            self._dispatch("DELETE")

    return Handler


def main():
    parser = argparse.ArgumentParser(description="HPLC instrument availability dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to share on the lab network")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database file")
    args = parser.parse_args()

    store = Store(args.db)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(store))
    print(f"HPLC dashboard running at http://{args.host}:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
