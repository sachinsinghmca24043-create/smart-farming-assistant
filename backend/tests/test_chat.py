
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_chat_returns_response():
    response = client.post(
        "/api/v1/chat",
        json={
            "message": "Hello",
            "language": "en",
            "session_id": None,
        },
    )

    assert response.status_code == 200

    data = response.json()
    assert data["answer"]
    assert data["language"] == "en"
    assert data["session_id"]


def test_unsupported_language_is_rejected():
    response = client.post(
        "/api/v1/chat",
        json={
            "message": "Hello",
            "language": "fr",
            "session_id": None,
        },
    )

    assert response.status_code == 422


def test_whitespace_message_is_rejected():
    response = client.post(
        "/api/v1/chat",
        json={
            "message": "   ",
            "language": "en",
            "session_id": None,
        },
    )

    assert response.status_code == 422
