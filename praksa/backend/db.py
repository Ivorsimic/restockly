import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.environ.get("DB_PATH", "praksa.db")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS internships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                title TEXT NOT NULL,
                company TEXT,
                location TEXT,
                remote INTEGER NOT NULL DEFAULT 0,
                kind TEXT NOT NULL,
                tags TEXT,
                url TEXT,
                description TEXT,
                posted_at TEXT,
                first_seen_at TEXT NOT NULL,
                contact_email TEXT,
                approved INTEGER NOT NULL DEFAULT 1,
                UNIQUE(source, external_id)
            );
            CREATE INDEX IF NOT EXISTS idx_internships_seen ON internships(first_seen_at);

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                keywords TEXT,
                location TEXT,
                remote_only INTEGER NOT NULL DEFAULT 0,
                kind TEXT,
                token TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS fetch_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ran_at TEXT NOT NULL,
                new_count INTEGER NOT NULL,
                status TEXT
            );
            """
        )
        conn.commit()


def upsert_internships(jobs: list[dict]) -> list[dict]:
    """Insert unseen jobs; return only the newly inserted ones (with their id)."""
    new = []
    ts = now()
    with get_conn() as conn:
        for j in jobs:
            cur = conn.execute(
                """INSERT OR IGNORE INTO internships
                   (source, external_id, title, company, location, remote, kind, tags, url, description,
                    posted_at, first_seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (j["source"], j["external_id"], j["title"], j.get("company"), j.get("location"),
                 int(bool(j.get("remote"))), j["kind"], j.get("tags"), j.get("url"), j.get("description"),
                 j.get("posted_at"), ts),
            )
            if cur.rowcount:
                new.append({**j, "id": cur.lastrowid})
        conn.commit()
    return new


def add_manual_internship(data: dict) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO internships
               (source, external_id, title, company, location, remote, kind, tags, url, description,
                posted_at, first_seen_at, contact_email, approved)
               VALUES ('Objavljeno ovdje', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (secrets.token_hex(8), data["title"], data["company"], data.get("location"),
             int(bool(data.get("remote"))), data.get("kind", "praksa"), data.get("tags"), data.get("url"),
             data.get("description"), now(), now(), data.get("contact_email")),
        )
        conn.commit()
        return cur.lastrowid


def _search_clause(q: str | None, location: str | None, remote_only: bool, kind: str | None,
                   source: str | None = None):
    where, params = ["approved = 1"], []
    for word in (q or "").split():
        where.append("(title LIKE ? OR company LIKE ? OR tags LIKE ? OR description LIKE ?)")
        params += [f"%{word}%"] * 4
    if location:
        where.append("location LIKE ?")
        params.append(f"%{location}%")
    if remote_only:
        where.append("remote = 1")
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if source:
        where.append("source = ?")
        params.append(source)
    return " AND ".join(where), params


def search(q=None, location=None, remote_only=False, kind=None, source=None, limit=50, offset=0):
    clause, params = _search_clause(q, location, remote_only, kind, source)
    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM internships WHERE {clause}", params).fetchone()[0]
        rows = conn.execute(
            f"""SELECT id, source, title, company, location, remote, kind, tags, url, description,
                       posted_at, first_seen_at, contact_email
                FROM internships WHERE {clause}
                ORDER BY COALESCE(posted_at, first_seen_at) DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
    return total, [dict(r) for r in rows]


def stats():
    with get_conn() as conn:
        by_source = conn.execute(
            "SELECT source, COUNT(*) n FROM internships WHERE approved = 1 GROUP BY source ORDER BY n DESC"
        ).fetchall()
        last = conn.execute("SELECT ran_at, new_count, status FROM fetch_runs ORDER BY id DESC LIMIT 1").fetchone()
    return {"by_source": {r["source"]: r["n"] for r in by_source}, "last_fetch": dict(last) if last else None}


def log_fetch(new_count: int, status: str):
    with get_conn() as conn:
        conn.execute("INSERT INTO fetch_runs (ran_at, new_count, status) VALUES (?,?,?)", (now(), new_count, status))
        conn.commit()


# ---------- alerts ----------

def create_alert(email, keywords, location, remote_only, kind) -> str:
    token = secrets.token_urlsafe(16)
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO alerts (email, keywords, location, remote_only, kind, token, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (email, keywords, location, int(remote_only), kind, token, now()),
        )
        conn.commit()
    return token


def deactivate_alert(token: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("UPDATE alerts SET active = 0 WHERE token = ? AND active = 1", (token,))
        conn.commit()
        return cur.rowcount > 0


def active_alerts():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM alerts WHERE active = 1").fetchall()]
