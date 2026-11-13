"""Response quality scoring and the human-review queue.

Every answer gets a 0-1 confidence score built from signals we already have
(intent confidence, retrieval similarity, whether the answer was grounded in a
verified source, whether live data was fresh). Answers below the threshold, or
that hit a known risk (no answer, unverified source, machine translation),
are saved to `flagged_responses` so extension workers can review them.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import closing
from dataclasses import dataclass, field
from typing import Any

from database import get_connection

logger = logging.getLogger(__name__)

# --- Tunables ---
try:
    REVIEW_THRESHOLD = float(os.getenv("QUALITY_THRESHOLD", "0.55"))
except ValueError:
    REVIEW_THRESHOLD = 0.55

# multilingual-e5 similarities are compressed: unrelated text still scores
# about 0.70-0.78, good matches about 0.85+. Map that band onto 0..1.
SIMILARITY_FLOOR = 0.70
SIMILARITY_CEILING = 0.90

WEIGHT_RETRIEVAL = 0.45
WEIGHT_INTENT = 0.25
WEIGHT_SOURCE = 0.15
WEIGHT_GROUNDING = 0.15

MAX_TEXT_CHARS = 4000
VALID_STATUSES = {"open", "reviewed", "dismissed"}


@dataclass
class QualityResult:
    score: float
    needs_review: bool
    reasons: list[str] = field(default_factory=list)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def score_response(
    *,
    route: str,
    kind: str = "answer",
    intent_confidence: float = 0.0,
    distance: float | None = None,
    sources_verified: bool | None = None,
    grounded: bool | None = None,
    machine_translated: bool = False,
    data_stale: bool = False,
) -> QualityResult:
    """Score one answer.

    kind: "answer" (a real answer), "clarification" (we asked the farmer for
          more details), "unavailable" (a live service failed), or "fallback"
          (nothing reliable to say).
    distance: cosine distance of the best retrieved passage (lower = better).
    """
    reasons: list[str] = []

    if kind == "clarification":
        return QualityResult(score=0.8, needs_review=False)

    if kind in {"fallback", "unavailable"}:
        reasons.append("no_answer" if kind == "fallback" else "live_service_unavailable")
        return QualityResult(score=0.0, needs_review=True, reasons=reasons)

    # Live data (weather / mandi prices) is authoritative when it arrives fresh.
    if route in {"weather", "market_prices"}:
        score = 0.6 if data_stale else 0.9
        if data_stale:
            reasons.append("stale_data")
        return QualityResult(
            score=round(score, 3),
            needs_review=(score < REVIEW_THRESHOLD or data_stale),
            reasons=reasons,
        )

    # Knowledge-base answers.
    if distance is None:
        retrieval = 0.5  # unknown (e.g. keyword fallback); stay neutral
        reasons.append("retrieval_score_unavailable")
    else:
        similarity = 1.0 - float(distance)
        retrieval = _clamp(
            (similarity - SIMILARITY_FLOOR) / (SIMILARITY_CEILING - SIMILARITY_FLOOR)
        )
        if retrieval < 0.4:
            reasons.append("weak_retrieval_match")

    intent = _clamp(float(intent_confidence))
    if intent < 0.35:
        reasons.append("low_intent_confidence")

    if sources_verified is None:
        source = 0.5
    elif sources_verified:
        source = 1.0
    else:
        source = 0.3
        reasons.append("unverified_source")

    if grounded is None:
        grounding = 0.6
    elif grounded:
        grounding = 1.0
    else:
        grounding = 0.0
        reasons.append("not_grounded")

    score = (
        WEIGHT_RETRIEVAL * retrieval
        + WEIGHT_INTENT * intent
        + WEIGHT_SOURCE * source
        + WEIGHT_GROUNDING * grounding
    )

    if machine_translated:
        score -= 0.05
        reasons.append("machine_translated")

    score = round(_clamp(score), 3)
    mandatory_review_reasons = {
        "no_answer",
        "live_service_unavailable",
        "unverified_source",
        "not_grounded",
        "low_intent_confidence",
        "machine_translated",
        "weak_retrieval_match",
        "retrieval_score_unavailable",
        "stale_data",
    }

    needs_review = (
        score < REVIEW_THRESHOLD
        or any(reason in mandatory_review_reasons for reason in reasons)
    )
    return QualityResult(
        score=score,
        needs_review=needs_review,
        reasons=reasons,
    )


# --------------------------------------------------------------------------
# Review queue (SQLite, same database file as the chat history)
# --------------------------------------------------------------------------
_table_ready = False


def _ensure_table() -> None:
    """Create the table on first use, so a missed startup step cannot break flagging."""
    if not _table_ready:
        init_quality_table()


def init_quality_table() -> None:
    """Create the review-queue table. Safe to call on every startup."""
    global _table_ready

    pk_col = "SERIAL PRIMARY KEY" if os.getenv("DATABASE_URL") else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with closing(get_connection()) as connection:
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS flagged_responses (
                id {pk_col},
                session_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now')),
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                intent TEXT,
                language TEXT,
                route TEXT,
                score REAL NOT NULL,
                reasons TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'open',
                reviewer_note TEXT,
                reviewed_at TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_flagged_status ON flagged_responses(status, id)"
        )

    _table_ready = True


def save_flag(
    *,
    session_id: str,
    question: str,
    answer: str,
    intent: str | None,
    language: str | None,
    route: str | None,
    result: QualityResult,
) -> int:
    """Add a low-confidence answer to the review queue. Returns the new id."""
    _ensure_table()
    with closing(get_connection()) as connection:
        cursor = connection.execute(
            """
            INSERT INTO flagged_responses
                (session_id, question, answer, intent, language, route, score, reasons)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                (question or "")[:MAX_TEXT_CHARS],
                (answer or "")[:MAX_TEXT_CHARS],
                intent,
                language,
                route,
                result.score,
                json.dumps(result.reasons),
            ),
        )
        return int(cursor.lastrowid)


def list_flags(status: str | None = "open", limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    _ensure_table()
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    query = (
        "SELECT id, session_id, created_at, question, answer, intent, language, route, "
        "score, reasons, status, reviewer_note, reviewed_at FROM flagged_responses"
    )
    params: list[Any] = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with closing(get_connection()) as connection:
        rows = connection.execute(query, params).fetchall()

    flags = []
    for row in rows:
        item = dict(row)
        try:
            item["reasons"] = json.loads(item["reasons"])
        except (TypeError, ValueError):
            item["reasons"] = []
        flags.append(item)
    return flags


def resolve_flag(flag_id: int, status: str = "reviewed", note: str | None = None) -> bool:
    """Mark a flagged answer as reviewed or dismissed. Returns False if not found."""
    if status not in VALID_STATUSES:
        raise ValueError(f"Status must be one of: {', '.join(sorted(VALID_STATUSES))}")

    _ensure_table()
    with closing(get_connection()) as connection:
        cursor = connection.execute(
            """
            UPDATE flagged_responses
            SET status = ?, reviewer_note = ?,
                reviewed_at = strftime('%Y-%m-%d %H:%M:%S', 'now')
            WHERE id = ?
            """,
            (status, (note or "")[:MAX_TEXT_CHARS] or None, int(flag_id)),
        )
        return cursor.rowcount > 0


def quality_stats() -> dict[str, Any]:
    """Counts for a simple admin dashboard."""
    _ensure_table()
    with closing(get_connection()) as connection:
        flag_rows = connection.execute(
            "SELECT status, COUNT(*) AS n FROM flagged_responses GROUP BY status"
        ).fetchall()
        chat_row = connection.execute(
            "SELECT COUNT(*) AS n FROM chat_history WHERE role = 'user'"
        ).fetchone()
        session_row = connection.execute(
            "SELECT COUNT(DISTINCT session_id) AS n FROM chat_history"
        ).fetchone()

    flags = {row["status"]: row["n"] for row in flag_rows}
    total_flags = sum(flags.values())
    questions = int(chat_row["n"]) if chat_row else 0

    return {
        "questions_total": questions,
        "sessions_total": int(session_row["n"]) if session_row else 0,
        "flagged_total": total_flags,
        "flagged_by_status": flags,
        "flag_rate": round(total_flags / questions, 3) if questions else 0.0,
        "review_threshold": REVIEW_THRESHOLD,
    }