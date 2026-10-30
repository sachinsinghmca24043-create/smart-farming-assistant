def generate_response(message: str, language: str = "en", session_id: str = None):
    return {
        "answer": f"Echo (Static Response): {message}",
        "language": language,
        "intent": "general",
        "confidence": 1.0,
    }
