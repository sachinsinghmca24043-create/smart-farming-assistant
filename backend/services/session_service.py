from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

from schemas.chat import ConversationMessage, ConversationState

logger = logging.getLogger(__name__)

# --- Tunables ---
MAX_SESSIONS = 1000            # Max concurrent sessions kept in memory
SESSION_TTL_SECONDS = 86400    # Expire after 24h of inactivity
MAX_MESSAGES_PER_SESSION = 100 # Cap history per session (prevents memory growth)
VALID_ROLES = frozenset({"user", "assistant", "system"})  # adjust to your schema


@dataclass(slots=True)
class _Entry:
    session: ConversationState
    last_access: float


class SessionStore:
    """In-memory LRU + TTL session store.

    Invariant: the OrderedDict is always ordered by last access (oldest first),
    so expired sessions are always at the front and cleanup is O(expired),
    not O(all sessions).
    """

    def __init__(
        self,
        max_sessions: int = MAX_SESSIONS,
        ttl_seconds: float = SESSION_TTL_SECONDS,
        max_messages: int = MAX_MESSAGES_PER_SESSION,
        clock: Callable[[], float] = time.monotonic,  # monotonic: immune to clock changes
    ) -> None:
        self._max_sessions = max_sessions
        self._ttl = ttl_seconds
        self._max_messages = max_messages
        self._clock = clock
        self._sessions: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.RLock()

    # ---------- internal helpers (call with lock held) ----------

    def _evict_expired(self, now: float) -> None:
        while self._sessions:
            _, entry = next(iter(self._sessions.items()))
            if now - entry.last_access <= self._ttl:
                break  # everything after this is newer
            sid, _ = self._sessions.popitem(last=False)
            logger.debug("Session %s expired", sid)

    def _make_room(self) -> None:
        while len(self._sessions) >= self._max_sessions:
            sid, _ = self._sessions.popitem(last=False)
            logger.info("Session %s evicted (LRU, capacity reached)", sid)

    def _touch(self, session_id: str, now: float) -> None:
        entry = self._sessions[session_id]
        entry.last_access = now
        self._sessions.move_to_end(session_id)

    @staticmethod
    def _normalize_id(session_id: str | None) -> str:
        sid = (session_id or "").strip()
        if not sid:
            raise ValueError("Session ID cannot be empty.")
        return sid

    # ---------- public API ----------

    def get_or_create(self, session_id: str, language: str) -> ConversationState:
        sid = self._normalize_id(session_id)
        with self._lock:
            now = self._clock()
            self._evict_expired(now)

            entry = self._sessions.get(sid)
            if entry is not None:
                entry.session.language = language
                self._touch(sid, now)
                return entry.session

            self._make_room()
            session = ConversationState(session_id=sid, language=language)
            self._sessions[sid] = _Entry(session, now)
            return session

    def get(self, session_id: str) -> ConversationState | None:
        if not session_id:
            return None
        sid = session_id.strip()
        with self._lock:
            now = self._clock()
            self._evict_expired(now)
            if sid not in self._sessions:
                return None
            self._touch(sid, now)
            return self._sessions[sid].session

    def add_message(self, session: ConversationState, role: str, content: str) -> None:
        if VALID_ROLES and role not in VALID_ROLES:
            raise ValueError(f"Invalid role: {role!r}")

        with self._lock:
            now = self._clock()
            sid = session.session_id

            if sid in self._sessions:
                self._touch(sid, now)
            else:
                # Session expired/evicted while the caller still held it:
                # re-register it so the conversation isn't silently lost.
                self._evict_expired(now)
                self._make_room()
                self._sessions[sid] = _Entry(session, now)

            session.messages.append(ConversationMessage(role=role, content=content))

            overflow = len(session.messages) - self._max_messages
            if overflow > 0:
                del session.messages[:overflow]  # drop oldest messages

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id.strip(), None) is not None

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def __len__(self) -> int:
        with self._lock:
            self._evict_expired(self._clock())
            return len(self._sessions)


# --- Default store + backward-compatible module-level functions ---
_store = SessionStore()


def get_or_create_session(session_id: str, language: str) -> ConversationState:
    return _store.get_or_create(session_id, language)


def add_message(session: ConversationState, role: str, content: str) -> None:
    _store.add_message(session, role, content)


def get_session(session_id: str) -> ConversationState | None:
    return _store.get(session_id)


def delete_session(session_id: str) -> bool:
    return _store.delete(session_id)