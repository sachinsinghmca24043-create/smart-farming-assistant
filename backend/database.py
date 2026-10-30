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
SCHEMA_VERSION = 2
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


def get_session_history(
    session_id: str,
    limit: int = 50,
    before_id: int | None = None,
) -> list[dict[str, Any]]:
    """Recent messages for a session, oldest first.

    before_id: pass the smallest `id` you already have to page further back.
    """
    if not session_id or not session_id.strip():
        return []

    try:
        limit = max(1, min(int(limit), MAX_HISTORY_LIMIT))
    except (TypeError, ValueError):
        limit = 50

    query = """
        SELECT id, role, message, language, intent, created_at
        FROM chat_history
        WHERE session_id = ?
    """
    params: list[Any] = [session_id.strip()]
    if before_id is not None:
        query += " AND id < ?"
        params.append(int(before_id))
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    with _connect() as connection:
        rows = connection.execute(query, params).fetchall()

    return [dict(row) for row in reversed(rows)]  # chronological order


def clear_session_history(session_id: str) -> bool:
    """Delete all messages for a session. Returns True if anything was deleted."""
    if not session_id or not session_id.strip():
        return False

    with _transaction() as connection:
        cursor = connection.execute(
            "DELETE FROM chat_history WHERE session_id = ?",
            (session_id.strip(),),
        )
        return cursor.rowcount > 0


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