from __future__ import annotations

import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("SMART_FARMING_DB_PATH", BASE_DIR / "smart_farming.db"))


def _get_database_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    return url


USE_POSTGRES = bool(_get_database_url())

# --- Tunables ---
BUSY_TIMEOUT_SECONDS = 15.0
SCHEMA_VERSION = 7
VALID_ROLES = frozenset({"user", "assistant", "system"})
MAX_SESSION_ID_CHARS = 128
MAX_MESSAGE_CHARS = 10_000
MAX_HISTORY_LIMIT = 200


# ---------------------------------------------------------------- Postgres Compatibility Adapter
def _translate_sql_for_pg(sql: str) -> str:
    """Translate SQLite-flavored SQL statements to PostgreSQL transparently."""
    out = sql.replace("?", "%s")
    out = out.replace("strftime('%Y-%m-%d %H:%M:%S', 'now')", "TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')")
    out = out.replace("date(created_at)", "SUBSTR(created_at, 1, 10)")
    out = re.sub(
        r"datetime\('now',\s*%s\)",
        "TO_CHAR((NOW() AT TIME ZONE 'UTC') + (%s)::interval, 'YYYY-MM-DD HH24:MI:SS')",
        out,
    )
    if "INSERT OR IGNORE INTO sessions" in out:
        out = out.replace("INSERT OR IGNORE INTO sessions", "INSERT INTO sessions")
        out = out.rstrip().rstrip(";") + " ON CONFLICT (session_id) DO NOTHING"
    return out


class _PgCursorWrapper:
    def __init__(self, cur: Any, lastrowid: int | None = None) -> None:
        self._cur = cur
        self.lastrowid = lastrowid
        self.rowcount = getattr(cur, "rowcount", 0)

    def fetchone(self) -> Any:
        return self._cur.fetchone()

    def fetchall(self) -> list[Any]:
        return self._cur.fetchall()


class _PgConnectionWrapper:
    def __init__(self, raw_conn: Any) -> None:
        self._conn = raw_conn

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> _PgCursorWrapper:
        pg_sql = _translate_sql_for_pg(sql)
        stripped = pg_sql.lstrip().upper()
        needs_returning = (
            stripped.startswith("INSERT INTO ")
            and "RETURNING " not in stripped
            and "INSERT INTO SESSIONS" not in stripped
            and "ON CONFLICT" not in stripped
        )
        if needs_returning:
            pg_sql = pg_sql.rstrip().rstrip(";") + " RETURNING id"

        cur = self._conn.cursor()
        cur.execute(pg_sql, tuple(params) if params is not None else None)
        lastrowid = None
        if needs_returning:
            row = cur.fetchone()
            if row and "id" in row:
                lastrowid = int(row["id"])
        return _PgCursorWrapper(cur, lastrowid=lastrowid)

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]) -> None:
        pg_sql = _translate_sql_for_pg(sql)
        cur = self._conn.cursor()
        cur.executemany(pg_sql, [tuple(p) for p in seq_of_params])

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------- connections
def get_connection() -> Any:
    """Return a configured DB connection (Neon PostgreSQL if DATABASE_URL is set, else SQLite)."""
    db_url = _get_database_url()
    if db_url:
        import psycopg
        from psycopg.rows import dict_row

        raw = psycopg.connect(db_url, row_factory=dict_row, autocommit=True)
        return _PgConnectionWrapper(raw)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        str(DB_PATH),
        timeout=BUSY_TIMEOUT_SECONDS,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA synchronous=NORMAL;")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def _connect() -> Iterator[Any]:
    connection = get_connection()
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def _transaction() -> Iterator[Any]:
    """Atomic write transaction across both PostgreSQL and SQLite."""
    with _connect() as connection:
        if isinstance(connection, _PgConnectionWrapper):
            connection._conn.autocommit = False
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
        else:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")


# ---------------------------------------------------------------- schema / migrations
def _migrate_postgres(connection: _PgConnectionWrapper) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id SERIAL PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            language TEXT,
            intent TEXT,
            created_at TEXT NOT NULL DEFAULT TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
        )
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_session_created
        ON chat_history(session_id, id)
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_created_at
        ON chat_history(created_at)
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS response_feedback (
            id SERIAL PRIMARY KEY,
            session_id TEXT NOT NULL,
            message_id INTEGER NOT NULL UNIQUE,
            rating TEXT NOT NULL CHECK (rating IN ('up', 'down')),
            created_at TEXT NOT NULL DEFAULT TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'),
            updated_at TEXT NOT NULL DEFAULT TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
        )
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_feedback_session
        ON response_feedback(session_id)
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'farmer' CHECK (role IN ('farmer', 'admin')),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL DEFAULT TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'),
            updated_at TEXT NOT NULL DEFAULT TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
        )
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_user
        ON sessions(user_id, updated_at)
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS auth_sessions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'),
            revoked_at TEXT
        )
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
        ON auth_sessions(user_id)
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry
        ON auth_sessions(expires_at)
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS llm_usage (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            session_id TEXT,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
        )
    """)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_created ON llm_usage(created_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_user ON llm_usage(user_id, created_at)")


def _migrate(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]

    if version < 1:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                language TEXT,
                intent TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_session_created
            ON chat_history(session_id, id)
        """)

    if version < 2:
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_created_at
            ON chat_history(created_at)
        """)

    if version < 4:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS response_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                message_id INTEGER NOT NULL UNIQUE,
                rating TEXT NOT NULL CHECK (rating IN ('up', 'down')),
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_feedback_session
            ON response_feedback(session_id)
        """)

    if version < 5:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'farmer'
                    CHECK (role IN ('farmer', 'admin')),
                is_active INTEGER NOT NULL DEFAULT 1
                    CHECK (is_active IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                user_id INTEGER,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_user
            ON sessions(user_id, updated_at)
        """)
        connection.execute("""
            INSERT OR IGNORE INTO sessions (session_id)
            SELECT DISTINCT session_id
            FROM chat_history
        """)

    if version < 6:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS auth_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now')),
                revoked_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        connection.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry ON auth_sessions(expires_at)")

    if version < 7:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS llm_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                session_id TEXT,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
            )
        """)
        connection.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_created ON llm_usage(created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_user ON llm_usage(user_id, created_at)")

    if version < SCHEMA_VERSION:
        connection.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")


def init_db() -> None:
    """Create/upgrade tables in Neon PostgreSQL or SQLite."""
    if _get_database_url():
        with _transaction() as connection:
            _migrate_postgres(connection)
        logger.info("Neon PostgreSQL database ready (schema v%d)", SCHEMA_VERSION)
        return

    with _connect() as connection:
        mode = connection.execute("PRAGMA journal_mode=WAL;").fetchone()[0]
        logger.info("SQLite journal mode: %s", mode)

    with _transaction() as connection:
        _migrate(connection)

    logger.info("SQLite database ready at %s (schema v%d)", DB_PATH, SCHEMA_VERSION)


def check_db() -> bool:
    """Cheap health check for /health endpoint."""
    try:
        with _connect() as connection:
            connection.execute("SELECT 1").fetchone()
        return True
    except Exception as exc:
        logger.error("Database health check failed: %s", exc)
        return False


# ---------------------------------------------------------------- validation
def _clean_session_id(session_id: str | None) -> str:
    sid = (session_id or "").strip()
    if not sid:
        raise ValueError("Session ID cannot be empty.")
    if len(sid) > MAX_SESSION_ID_CHARS:
        raise ValueError(f"Session ID exceeds {MAX_SESSION_ID_CHARS} characters.")
    return sid


def _clean_message_fields(role: str | None, message: str | None) -> tuple[str, str]:
    role = (role or "").strip().lower()
    if VALID_ROLES and role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role!r}")

    message = (message or "").strip()
    if not message:
        raise ValueError("Message cannot be empty.")
    if len(message) > MAX_MESSAGE_CHARS:
        message = message[:MAX_MESSAGE_CHARS]
    return role, message


# ---------------------------------------------------------------- operations
def save_message(
    session_id: str,
    role: str,
    message: str,
    language: str | None = "en",
    intent: str | None = None,
) -> int:
    sid = _clean_session_id(session_id)
    role, message = _clean_message_fields(role, message)

    with _transaction() as connection:
        cursor = connection.execute(
            """
            INSERT INTO chat_history (session_id, role, message, language, intent)
            VALUES (?, ?, ?, ?, ?)
            """,
            (sid, role, message, language, intent),
        )
        return int(cursor.lastrowid)


def save_messages(session_id: str, messages: Iterable[dict[str, Any]]) -> int:
    sid = _clean_session_id(session_id)
    rows = []
    for item in messages:
        role, message = _clean_message_fields(item.get("role"), item.get("message"))
        rows.append((sid, role, message, item.get("language", "en"), item.get("intent")))

    if not rows:
        return 0

    with _transaction() as connection:
        connection.executemany(
            """
            INSERT INTO chat_history (session_id, role, message, language, intent)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def ensure_session_owner(session_id: str, user_id: int) -> bool:
    """Create a new farmer-owned session or verify its existing owner."""
    sid = _clean_session_id(session_id)

    if not user_id:
        return False

    with _transaction() as connection:
        row = connection.execute(
            "SELECT user_id FROM sessions WHERE session_id = ?",
            (sid,),
        ).fetchone()

        if row is None:
            connection.execute(
                "INSERT INTO sessions (session_id, user_id) VALUES (?, ?)",
                (sid, user_id),
            )
            return True

        return row["user_id"] == user_id


def save_chat_turn(
    session_id: str,
    language: str,
    intent: str,
    user_message: str,
    assistant_message: str,
    user_id: int,
) -> int:
    """Save a complete chat turn strictly for the owning farmer."""
    sid = _clean_session_id(session_id)

    user_role, user_text = _clean_message_fields("user", user_message)
    assistant_role, assistant_text = _clean_message_fields("assistant", assistant_message)

    with _transaction() as connection:
        session = connection.execute(
            "SELECT user_id FROM sessions WHERE session_id = ?",
            (sid,),
        ).fetchone()

        if session is None or session["user_id"] != user_id:
            raise PermissionError("You do not own this conversation.")

        connection.execute(
            """
            INSERT INTO chat_history (session_id, role, message, language, intent)
            VALUES (?, ?, ?, ?, ?)
            """,
            (sid, user_role, user_text, language, intent),
        )

        cursor = connection.execute(
            """
            INSERT INTO chat_history (session_id, role, message, language, intent)
            VALUES (?, ?, ?, ?, ?)
            """,
            (sid, assistant_role, assistant_text, language, intent),
        )

        connection.execute(
            """
            UPDATE sessions
            SET updated_at = strftime('%Y-%m-%d %H:%M:%S', 'now')
            WHERE session_id = ?
            """,
            (sid,),
        )

        return int(cursor.lastrowid)


def get_session_history(
    session_id: str,
    user_id: int,
    limit: int = 50,
    before_id: int | None = None,
) -> list[dict[str, Any]]:
    """Return history only when the requesting farmer owns the session."""
    if not session_id or not session_id.strip() or not user_id:
        return []

    try:
        limit = max(1, min(int(limit), MAX_HISTORY_LIMIT))
    except (TypeError, ValueError):
        limit = 50

    query = """
        SELECT id, role, message, language, intent, created_at
        FROM chat_history
        WHERE session_id = ?
          AND EXISTS (
              SELECT 1 FROM sessions
              WHERE sessions.session_id = chat_history.session_id
                AND sessions.user_id = ?
          )
    """
    params: list[Any] = [session_id.strip(), user_id]

    if before_id is not None:
        query += " AND id < ?"
        params.append(int(before_id))

    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    with _connect() as connection:
        rows = connection.execute(query, params).fetchall()

    return [dict(row) for row in reversed(rows)]


def get_farmer_sessions(user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    """List all sessions owned exclusively by the authenticated farmer."""
    if not user_id:
        return []
    limit = max(1, min(int(limit), 100))
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT s.session_id, s.created_at, s.updated_at,
                   (SELECT message FROM chat_history ch
                    WHERE ch.session_id = s.session_id AND ch.role = 'user'
                    ORDER BY ch.id ASC LIMIT 1) AS first_question,
                   (SELECT COUNT(*) FROM chat_history ch
                    WHERE ch.session_id = s.session_id) AS message_count
            FROM sessions s
            WHERE s.user_id = ?
            ORDER BY s.updated_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def clear_session_history(session_id: str, user_id: int) -> bool:
    """Delete conversation history only if the farmer owns the session."""
    if not session_id or not session_id.strip() or not user_id:
        return False

    sid = session_id.strip()

    with _transaction() as connection:
        session = connection.execute(
            "SELECT user_id FROM sessions WHERE session_id = ?",
            (sid,),
        ).fetchone()

        if session is None or session["user_id"] != user_id:
            return False

        connection.execute("DELETE FROM chat_history WHERE session_id = ?", (sid,))
        connection.execute("DELETE FROM sessions WHERE session_id = ? AND user_id = ?", (sid, user_id))
        return True


def prune_old_history(max_age_days: int = 30) -> int:
    days = int(max_age_days)
    if days < 1:
        raise ValueError("max_age_days must be at least 1.")

    with _transaction() as connection:
        cursor = connection.execute(
            "DELETE FROM chat_history WHERE created_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        deleted = cursor.rowcount

    if deleted:
        logger.info("Pruned %d chat messages older than %d days.", deleted, days)
    return deleted


def save_feedback(
    session_id: str,
    message_id: int,
    rating: str,
    user_id: int,
) -> bool:
    sid = _clean_session_id(session_id)
    rating = (rating or "").strip().lower()

    if rating not in {"up", "down"}:
        raise ValueError("Rating must be 'up' or 'down'.")

    try:
        message_id = int(message_id)
    except (TypeError, ValueError):
        raise ValueError("Invalid message ID.")

    if message_id <= 0:
        raise ValueError("Invalid message ID.")

    with _transaction() as connection:
        answer = connection.execute(
            """
            SELECT ch.id
            FROM chat_history AS ch
            JOIN sessions AS s ON s.session_id = ch.session_id
            WHERE ch.id = ?
              AND ch.session_id = ?
              AND ch.role = 'assistant'
              AND s.user_id = ?
            """,
            (message_id, sid, user_id),
        ).fetchone()

        if answer is None:
            raise ValueError("Assistant message not found.")

        connection.execute(
            """
            INSERT INTO response_feedback (session_id, message_id, rating)
            VALUES (?, ?, ?)
            ON CONFLICT(message_id)
            DO UPDATE SET
                rating = excluded.rating,
                updated_at = strftime('%Y-%m-%d %H:%M:%S', 'now')
            """,
            (sid, message_id, rating),
        )

    return True


def get_feedback_stats() -> dict[str, int | float]:
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total_feedback,
                COALESCE(SUM(CASE WHEN rating = 'up' THEN 1 ELSE 0 END), 0) AS positive,
                COALESCE(SUM(CASE WHEN rating = 'down' THEN 1 ELSE 0 END), 0) AS negative
            FROM response_feedback
            """
        ).fetchone()

    total = int(row["total_feedback"] or 0)
    positive = int(row["positive"] or 0)
    negative = int(row["negative"] or 0)
    percentage = round((positive / total) * 100, 2) if total else 0.0

    return {
        "total_feedback": total,
        "positive": positive,
        "negative": negative,
        "positive_percentage": percentage,
    }


# ---------------------------------------------------------------- admin analytics
def record_llm_usage(
    provider: str,
    model: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None,
    user_id: int | None = None,
    session_id: str | None = None,
) -> None:
    values = (
        str(provider)[:40],
        str(model)[:120],
        max(0, int(prompt_tokens or 0)),
        max(0, int(completion_tokens or 0)),
        max(0, int(total_tokens or 0)),
        int(user_id) if user_id is not None else None,
        str(session_id)[:MAX_SESSION_ID_CHARS] if session_id else None,
    )
    with _transaction() as connection:
        connection.execute(
            """INSERT INTO llm_usage
            (provider, model, prompt_tokens, completion_tokens, total_tokens, user_id, session_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            values,
        )


def get_admin_farmer_list(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
    with _connect() as connection:
        total = connection.execute("SELECT COUNT(*) AS n FROM users WHERE role='farmer'").fetchone()["n"]
        rows = connection.execute(
            """SELECT u.id, u.name, u.email, u.is_active, u.created_at,
            (SELECT COUNT(*) FROM sessions s WHERE s.user_id=u.id) AS sessions_count,
            (SELECT COUNT(*) FROM chat_history ch JOIN sessions s ON s.session_id=ch.session_id WHERE s.user_id=u.id) AS messages_count,
            (SELECT COALESCE(SUM(lu.total_tokens),0) FROM llm_usage lu WHERE lu.user_id=u.id) AS total_tokens
            FROM users u WHERE u.role='farmer' ORDER BY u.created_at DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
    return {"total": int(total), "farmers": [dict(r) for r in rows]}


def get_admin_query_list(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
    with _connect() as connection:
        total = connection.execute("SELECT COUNT(*) AS n FROM chat_history WHERE role='user'").fetchone()["n"]
        rows = connection.execute(
            """SELECT ch.id, ch.session_id, ch.message AS question,
            ch.language, ch.intent, ch.created_at, u.id AS farmer_id,
            u.name AS farmer_name, u.email AS farmer_email
            FROM chat_history ch LEFT JOIN sessions s ON s.session_id=ch.session_id
            LEFT JOIN users u ON u.id=s.user_id WHERE ch.role='user'
            ORDER BY ch.id DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
    return {"total": int(total), "queries": [dict(r) for r in rows]}


def get_admin_token_usage() -> dict[str, Any]:
    with _connect() as connection:
        totals = connection.execute(
            """SELECT COUNT(*) AS requests,
            COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,
            COALESCE(SUM(completion_tokens),0) AS completion_tokens,
            COALESCE(SUM(total_tokens),0) AS total_tokens FROM llm_usage"""
        ).fetchone()
        by_model = connection.execute(
            """SELECT provider, model, COUNT(*) AS requests,
            SUM(prompt_tokens) AS prompt_tokens, SUM(completion_tokens) AS completion_tokens,
            SUM(total_tokens) AS total_tokens FROM llm_usage GROUP BY provider, model
            ORDER BY total_tokens DESC"""
        ).fetchall()
        daily = connection.execute(
            """SELECT date(created_at) AS day, SUM(total_tokens) AS total_tokens
            FROM llm_usage GROUP BY date(created_at) ORDER BY day DESC LIMIT 30"""
        ).fetchall()
    return {
        "totals": dict(totals),
        "by_model": [dict(r) for r in by_model],
        "daily": [dict(r) for r in daily],
    }
