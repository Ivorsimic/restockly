import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.environ.get("DB_PATH", "praksa.db")
SCHEMA_VERSION = 2
MANUAL_SOURCE = "Objavljeno ovdje"


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
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            # v1 was a prototype with foreign sources; nothing worth migrating.
            conn.executescript("DROP TABLE IF EXISTS internships; DROP TABLE IF EXISTS alerts; DROP TABLE IF EXISTS fetch_runs;")
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS internships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                title TEXT NOT NULL,
                company TEXT,
                location TEXT,
                city TEXT,
                remote INTEGER NOT NULL DEFAULT 0,
                kind TEXT NOT NULL,
                category TEXT,
                pay TEXT,
                work_period TEXT,
                tags TEXT,
                url TEXT,
                description TEXT,
                posted_at TEXT,
                expires_at TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                contact_email TEXT,
                active INTEGER NOT NULL DEFAULT 1,       -- 0 = removed at the source
                approved INTEGER NOT NULL DEFAULT 1,     -- 0 = company post waiting for moderation
                moderation_token TEXT,
                UNIQUE(source, external_id)
            );
            CREATE INDEX IF NOT EXISTS idx_internships_visible ON internships(active, approved, expires_at);

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                keywords TEXT,
                city TEXT,
                category TEXT,
                kind TEXT,
                remote_only INTEGER NOT NULL DEFAULT 0,
                token TEXT NOT NULL UNIQUE,
                confirmed INTEGER NOT NULL DEFAULT 0,    -- double opt-in
                created_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS fetch_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ran_at TEXT NOT NULL,
                new_count INTEGER NOT NULL,
                status TEXT
            );
            PRAGMA user_version = {SCHEMA_VERSION};
            """
        )
        conn.commit()


JOB_FIELDS = ["title", "company", "location", "city", "remote", "kind", "category", "pay", "work_period", "tags",
              "url", "description", "posted_at", "expires_at"]


def sync_source(source: str, jobs: list[dict], full_sync: bool) -> list[dict]:
    """Insert new postings, refresh existing ones, and (for full-sync sources) deactivate postings
    that disappeared from the source. Returns newly inserted jobs."""
    new, ts = [], now()
    with get_conn() as conn:
        for j in jobs:
            values = [int(bool(j.get(f))) if f == "remote" else j.get(f) for f in JOB_FIELDS]
            cur = conn.execute(
                f"""INSERT INTO internships (source, external_id, {", ".join(JOB_FIELDS)}, first_seen_at, last_seen_at)
                    VALUES (?, ?, {", ".join("?" * len(JOB_FIELDS))}, ?, ?)
                    ON CONFLICT(source, external_id) DO NOTHING""",
                [source, j["external_id"], *values, ts, ts],
            )
            if cur.rowcount:
                new.append({**j, "id": cur.lastrowid})
            else:
                conn.execute(
                    f"""UPDATE internships SET {", ".join(f"{f} = ?" for f in JOB_FIELDS)}, last_seen_at = ?, active = 1
                        WHERE source = ? AND external_id = ?""",
                    [*values, ts, source, j["external_id"]],
                )
        if full_sync and jobs:
            conn.execute("UPDATE internships SET active = 0 WHERE source = ? AND last_seen_at < ?", (source, ts))
        conn.commit()
    return new


# ---------- company posts + moderation ----------

def add_manual_internship(data: dict) -> tuple[int, str]:
    token = secrets.token_urlsafe(16)
    with get_conn() as conn:
        cur = conn.execute(
            f"""INSERT INTO internships (source, external_id, {", ".join(JOB_FIELDS)}, first_seen_at, last_seen_at,
                                         contact_email, approved, moderation_token)
                VALUES (?, ?, {", ".join("?" * len(JOB_FIELDS))}, ?, ?, ?, 0, ?)""",
            [MANUAL_SOURCE, secrets.token_hex(8),
             *[int(bool(data.get(f))) if f == "remote" else data.get(f) for f in JOB_FIELDS],
             now(), now(), data.get("contact_email"), token],
        )
        conn.commit()
        return cur.lastrowid, token


def moderate(token: str, approve: bool) -> dict | None:
    """Approve or reject a pending company post. Returns the post if the token was valid and pending."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM internships WHERE moderation_token = ? AND approved = 0 AND active = 1", (token,)
        ).fetchone()
        if not row:
            return None
        if approve:
            conn.execute("UPDATE internships SET approved = 1, posted_at = ? WHERE id = ?", (now(), row["id"]))
        else:
            conn.execute("UPDATE internships SET active = 0 WHERE id = ?", (row["id"],))
        conn.commit()
        return dict(row)


def pending_posts():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM internships WHERE approved = 0 AND active = 1 ORDER BY id").fetchall()]


# ---------- search ----------

VISIBLE = "active = 1 AND approved = 1 AND (expires_at IS NULL OR expires_at > ?)"


def search(q=None, city=None, category=None, kind=None, remote_only=False, source=None, limit=30, offset=0):
    where, params = [VISIBLE], [now()]
    for word in (q or "").split():
        where.append("(title LIKE ? OR company LIKE ? OR tags LIKE ? OR description LIKE ? OR location LIKE ?)")
        params += [f"%{word}%"] * 5
    for col, val in (("city", city), ("category", category), ("kind", kind), ("source", source)):
        if val:
            where.append(f"{col} = ?")
            params.append(val)
    if remote_only:
        where.append("remote = 1")
    clause = " AND ".join(where)
    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM internships WHERE {clause}", params).fetchone()[0]
        rows = conn.execute(
            f"""SELECT id, source, title, company, location, city, remote, kind, category, pay, work_period, tags,
                       url, description, posted_at, expires_at, first_seen_at, contact_email
                FROM internships WHERE {clause}
                ORDER BY (kind = 'praksa') DESC, COALESCE(posted_at, first_seen_at) DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
    return total, [dict(r) for r in rows]


def stats():
    with get_conn() as conn:
        def grouped(col):
            return {r[0]: r[1] for r in conn.execute(
                f"SELECT {col}, COUNT(*) n FROM internships WHERE {VISIBLE} AND {col} IS NOT NULL "
                f"GROUP BY {col} ORDER BY n DESC", (now(),)).fetchall()}
        out = {"by_source": grouped("source"), "by_city": grouped("city"), "by_kind": grouped("kind")}
        last = conn.execute("SELECT ran_at, new_count, status FROM fetch_runs ORDER BY id DESC LIMIT 1").fetchone()
    out["last_fetch"] = dict(last) if last else None
    return out


def log_fetch(new_count: int, status: str):
    with get_conn() as conn:
        conn.execute("INSERT INTO fetch_runs (ran_at, new_count, status) VALUES (?,?,?)", (now(), new_count, status))
        conn.commit()


# ---------- alerts (double opt-in) ----------

def create_alert(email, keywords, city, category, kind, remote_only) -> str:
    token = secrets.token_urlsafe(16)
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO alerts (email, keywords, city, category, kind, remote_only, token, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (email, keywords, city, category, kind, int(remote_only), token, now()),
        )
        conn.commit()
    return token


def confirm_alert(token: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("UPDATE alerts SET confirmed = 1 WHERE token = ? AND active = 1", (token,))
        conn.commit()
        return cur.rowcount > 0


def deactivate_alert(token: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("UPDATE alerts SET active = 0 WHERE token = ? AND active = 1", (token,))
        conn.commit()
        return cur.rowcount > 0


def active_alerts():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM alerts WHERE active = 1 AND confirmed = 1").fetchall()]
