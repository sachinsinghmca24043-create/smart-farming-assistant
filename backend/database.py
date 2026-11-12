from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("SMART_FARMING_DB_PATH", BASE_DIR / "smart_farming.db"))

# --- Tunables ---
BUSY_TIMEOUT_SECONDS = 15.0
SCHEMA_VERSION = 7
VALID_ROLES = frozenset({"user", "assistant", "system"})  # adjust to your app
MAX_SESSION_ID_CHARS = 128
MAX_MESSAGE_CHARS = 10_000
MAX_HISTORY_LIMIT = 200


# ---------------------------------------------------------------- connections
def get_connection() -> sqlite3.Connection:
    """Return a new configured connection. The CALLER must close it.

    Kept for backward compatibility; inside this module use _connect()/_transaction(),
    which always close the connection. (Note: `with sqlite3.connect(...)` only
    commits/rolls back, it does NOT close, so the old code leaked a connection per call.)
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None -> autocommit; transactions are explicit (see _transaction).
    connection = sqlite3.connect(
        str(DB_PATH),
        timeout=BUSY_TIMEOUT_SECONDS,  # also sets SQLite's busy_timeout
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA synchronous=NORMAL;")  # per-connection setting; safe with WAL
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    connection = get_connection()
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def _transaction() -> Iterator[sqlite3.Connection]:
    """Write transaction. BEGIN IMMEDIATE takes the write lock up front, so
    concurrent writers (threads or multiple worker processes) queue on the busy
    timeout instead of failing mid-transaction with 'database is locked'."""
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        else:
            connection.execute("COMMIT")


# ---------------------------------------------------------------- schema / migrations
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
        # Speeds up retention pruning by age.
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
                created_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%d %H:%M:%S', 'now')
                ),
                updated_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%d %H:%M:%S', 'now')
                )
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
                created_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%d %H:%M:%S', 'now')
                )
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                user_id INTEGER,
                created_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%d %H:%M:%S', 'now')
                ),
                updated_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%d %H:%M:%S', 'now')
                ),
                FOREIGN KEY (user_id) REFERENCES users(id)
                    ON DELETE CASCADE
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_user
            ON sessions(user_id, updated_at)
        """)

        # Preserve existing conversations as unassigned legacy sessions.
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
                created_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%d %H:%M:%S', 'now')
                ),
                revoked_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
                    ON DELETE CASCADE
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

    # Future changes: add `if version < 3:` blocks above and bump SCHEMA_VERSION.
    if version < SCHEMA_VERSION:
        connection.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")


def init_db() -> None:
    """Create/upgrade tables. Safe to call on every startup, from any worker."""
    with _connect() as connection:
        # WAL is stored in the DB file, so set it once here (cannot run inside a transaction).
        mode = connection.execute("PRAGMA journal_mode=WAL;").fetchone()[0]
        logger.info("SQLite journal mode: %s", mode)

    with _transaction() as connection:
        _migrate(connection)

    logger.info("SQLite database ready at %s (schema v%d)", DB_PATH, SCHEMA_VERSION)


def check_db() -> bool:
    """Cheap health check for a /health endpoint."""
    try:
        with _connect() as connection:
            connection.execute("SELECT 1").fetchone()
        return True
    except sqlite3.Error as exc:
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
        message = message[:MAX_MESSAGE_CHARS]  # truncate rather than lose the turn
    return role, message


# ---------------------------------------------------------------- operations
def save_message(
    session_id: str,
    role: str,
    message: str,
    language: str | None = "en",
    intent: str | None = None,
) -> int:
    """Persist one chat message. Returns the new row id."""
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
    """Save several messages atomically (e.g. the user question and the assistant
    answer together, so you never store half a conversation turn).

    Each item: {"role": ..., "message": ..., "language": optional, "intent": optional}
    """
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
    """Save a complete chat turn for an owned session."""
    sid = _clean_session_id(session_id)

    user_role, user_text = _clean_message_fields("user", user_message)
    assistant_role, assistant_text = _clean_message_fields(
        "assistant", assistant_message
    )

    with _transaction() as connection:
        session = connection.execute(
            "SELECT user_id FROM sessions WHERE session_id = ?",
            (sid,),
        ).fetchone()

        if session is None or session["user_id"] != user_id:
            raise PermissionError("You do not own this conversation.")

        connection.execute(
            """
            INSERT INTO chat_history (
                session_id, role, message, language, intent
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (sid, user_role, user_text, language, intent),
        )

        cursor = connection.execute(
            """
            INSERT INTO chat_history (
                session_id, role, message, language, intent
            )
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

        cursor = connection.execute(
            "DELETE FROM chat_history WHERE session_id = ?",
            (sid,),
        )

        connection.execute(
            "DELETE FROM sessions WHERE session_id = ? AND user_id = ?",
            (sid, user_id),
        )

        return True



def prune_old_history(max_age_days: int = 30) -> int:
    """Delete messages older than max_age_days (timestamps are UTC). Returns rows deleted.

    Run this on a schedule or at startup so the database (which holds farmers'
    questions) doesn't grow forever.
    """
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
    """Save or update feedback for one assistant response."""
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
            INSERT INTO response_feedback (
                session_id, message_id, rating
            )
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
    """Return aggregated feedback statistics for the admin dashboard."""
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total_feedback,
                SUM(CASE WHEN rating = 'up' THEN 1 ELSE 0 END) AS positive,
                SUM(CASE WHEN rating = 'down' THEN 1 ELSE 0 END) AS negative
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
def record_llm_usage(provider: str, model: str, prompt_tokens: int | None,
                     completion_tokens: int | None, total_tokens: int | None,
                     user_id: int | None = None, session_id: str | None = None) -> None:
    """Persist provider-reported token usage; missing counts are stored as zero."""
    values = (str(provider)[:40], str(model)[:120], max(0, int(prompt_tokens or 0)),
              max(0, int(completion_tokens or 0)), max(0, int(total_tokens or 0)),
              int(user_id) if user_id is not None else None,
              str(session_id)[:MAX_SESSION_ID_CHARS] if session_id else None)
    with _transaction() as connection:
        connection.execute("""INSERT INTO llm_usage
            (provider, model, prompt_tokens, completion_tokens, total_tokens, user_id, session_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)""", values)


def get_admin_farmer_list(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
    with _connect() as connection:
        total = connection.execute("SELECT COUNT(*) AS n FROM users WHERE role='farmer'").fetchone()["n"]
        rows = connection.execute("""SELECT u.id, u.name, u.email, u.is_active, u.created_at,
            (SELECT COUNT(*) FROM sessions s WHERE s.user_id=u.id) AS sessions_count,
            (SELECT COUNT(*) FROM chat_history ch JOIN sessions s ON s.session_id=ch.session_id WHERE s.user_id=u.id) AS messages_count,
            (SELECT COALESCE(SUM(lu.total_tokens),0) FROM llm_usage lu WHERE lu.user_id=u.id) AS total_tokens
            FROM users u WHERE u.role='farmer' ORDER BY u.created_at DESC LIMIT ? OFFSET ?""",
            (limit, offset)).fetchall()
    return {"total": int(total), "farmers": [dict(r) for r in rows]}


def get_admin_query_list(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
    with _connect() as connection:
        total = connection.execute("SELECT COUNT(*) AS n FROM chat_history WHERE role='user'").fetchone()["n"]
        rows = connection.execute("""SELECT ch.id, ch.session_id, ch.message AS question,
            ch.language, ch.intent, ch.created_at, u.id AS farmer_id,
            u.name AS farmer_name, u.email AS farmer_email
            FROM chat_history ch LEFT JOIN sessions s ON s.session_id=ch.session_id
            LEFT JOIN users u ON u.id=s.user_id WHERE ch.role='user'
            ORDER BY ch.id DESC LIMIT ? OFFSET ?""", (limit, offset)).fetchall()
    return {"total": int(total), "queries": [dict(r) for r in rows]}


def get_admin_token_usage() -> dict[str, Any]:
    with _connect() as connection:
        totals = connection.execute("""SELECT COUNT(*) AS requests,
            COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,
            COALESCE(SUM(completion_tokens),0) AS completion_tokens,
            COALESCE(SUM(total_tokens),0) AS total_tokens FROM llm_usage""").fetchone()
        by_model = connection.execute("""SELECT provider, model, COUNT(*) AS requests,
            SUM(prompt_tokens) AS prompt_tokens, SUM(completion_tokens) AS completion_tokens,
            SUM(total_tokens) AS total_tokens FROM llm_usage GROUP BY provider, model
            ORDER BY total_tokens DESC""").fetchall()
        daily = connection.execute("""SELECT date(created_at) AS day, SUM(total_tokens) AS total_tokens
            FROM llm_usage GROUP BY date(created_at) ORDER BY day DESC LIMIT 30""").fetchall()
    return {"totals": dict(totals), "by_model": [dict(r) for r in by_model],
            "daily": [dict(r) for r in daily]}
