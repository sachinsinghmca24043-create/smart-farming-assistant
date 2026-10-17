
from schemas.chat import ConversationMessage, ConversationState

# Temporary session storage for development.
_sessions: dict[str, ConversationState] = {}


def get_or_create_session(
    session_id: str,
    language: str,
) -> ConversationState:
    """Return an existing session or create a new one."""
    session = _sessions.get(session_id)

    if session is None:
        session = ConversationState(
            session_id=session_id,
            language=language,
        )
        _sessions[session_id] = session
    else:
        session.language = language

    return session


def add_message(
    session: ConversationState,
    role: str,
    content: str,
) -> None:
    """Append a message to the session history."""
    session.messages.append(
        ConversationMessage(role=role, content=content)
    )


def get_session(session_id: str) -> ConversationState | None:
    """Return a session if it exists."""
    return _sessions.get(session_id)
