
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from uuid import uuid4
from fastapi import File, HTTPException, UploadFile
from services.speech_service import transcribe_audio
from schemas.chat import ChatRequest, ChatResponse
from services.intent_service import analyze_intent
from services.response_service import generate_response
from services.knowledge_service import search_knowledge
from services.session_service import (
    get_or_create_session,
    add_message,)
from langdetect import detect, LangDetectException
app = FastAPI(
    title="Smart Farming Assistant API",
    description="Backend for multilingual agricultural guidance",
    version="1.0.0",
)

# Allow requests from the Next.js development server
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def home():
    return {
        "message": "Smart Farming Assistant API is running"
    }


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "smart-farming-backend",
    }

@app.post("/api/v1/detect-language")
def detect_language_endpoint(request: dict):
    text = request.get("text", "").strip()

    if not text:
        raise HTTPException(
            status_code=400,
            detail="Text cannot be empty.",
        )

    try:
        detected_language = detect(text)
    except LangDetectException:
        detected_language = "unknown"

    if detected_language not in {"hi", "kn", "en"}:
        detected_language = "unknown"

    return {"language": detected_language}




@app.post("/api/v1/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    session_id = request.session_id or str(uuid4())

    session = get_or_create_session(session_id, request.language)
    session.preferences["language"] = request.language

    add_message(session, "user", request.message)

    # Analyze the original message using the multilingual classifier.
    analysis = analyze_intent(request.message, request.language)

    # Reuse the crop mentioned earlier in the conversation.
    if not analysis["entities"]["crops"]:
        previous_crop = session.context.get("crop")
        if previous_crop:
            analysis["entities"]["crops"] = [previous_crop]

    crop = (
        analysis["entities"]["crops"][0]
        if analysis["entities"]["crops"]
        else None
    )
    intent = analysis["intent"]

    # Search the English knowledge base only when the message
    # contains enough English keywords to support a useful match.
    knowledge_results = search_knowledge(
    query=request.message,
    crop=crop,
    topic=intent,
    limit=3,
)

    selected_record = knowledge_results[0] if knowledge_results else None

    if selected_record and request.language == "en":
        answer = selected_record["content"]
    elif selected_record and request.language in selected_record.get("translations", {}):
        answer = selected_record["translations"][request.language]
    else:
        # Never present English content as if it were translated.
        selected_record = None
        answer = generate_response(intent, request.language)

    if selected_record:
        source_name = selected_record.get("source_name")
        source_url = selected_record.get("source_url") or None
    else:
        source_name = None
        source_url = None

    if crop:
        session.context["crop"] = crop

    session.context["last_intent"] = intent
    add_message(session, "assistant", answer)

    return ChatResponse(
        answer=answer,
        language=request.language,
        session_id=session_id,
        intent=intent,
        confidence=analysis["confidence"],
        entities=analysis["entities"],
        classification_model=analysis["classification_model"],
        fallback_used=analysis["fallback_used"],
        source_name=source_name,
        source_url=source_url,
    )







@app.post("/api/v1/speech/transcribe")
def speech_to_text(file: UploadFile = File(...)):
    try:
        audio_bytes = file.file.read(10 * 1024 * 1024 + 1)
        result = transcribe_audio(
            audio_bytes=audio_bytes,
            filename=file.filename or "",
        )
        return {
            "status": "success",
            **result,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        file.file.close()
