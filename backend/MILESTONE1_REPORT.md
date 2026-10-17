# AI-Driven Smart Farming Assistant
## Milestone 1 — Implementation and Evaluation Report

### 1. Objective
Build a prototype multilingual agricultural assistant that accepts text and speech,
identifies user intent and agricultural entities, and returns structured responses.

### 2. Current Architecture
- Frontend: Next.js, React, TypeScript.
- Backend: Python and FastAPI.
- Speech recognition: Whisper for language detection/English fallback and
  IndicConformer through sherpa-onnx for supported Indian-language transcription.
- Language detection: langdetect and the existing speech pipeline.
- Intent classification: Hugging Face zero-shot classification using
  MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli.
- Entity extraction: dictionary-based multilingual crop, topic and location aliases.
- Knowledge retrieval: local JSON knowledge base and keyword matching.
- Session handling: backend session service.
- Text-to-speech: browser SpeechSynthesis API.
- Tests: pytest.

### 3. Chat Response Schema
The chat response includes:
- answer
- language
- session_id
- intent
- confidence
- entities
- classification_model
- fallback_used
- source_name
- source_url

The request supports message, language, and an optional session_id.

### 4. Intent Evaluation
Evaluation script: evaluate_intents.py
Generated report: intent_evaluation_report.json

Current reported result:
- Queries: 65
- Correct: 65
- Incorrect: 0
- Accuracy on this test set: 100%

Important limitation:
The evaluation questions overlap with the manually added keyword rules.
This result measures performance on the current test set only and is not
an independent estimate of real-world accuracy. Further unseen, ambiguous,
unrelated and paraphrased questions must be evaluated.

The earlier baseline was 51/65 (78.46%). The later result followed keyword
rule changes, so it does not establish that the underlying ML model alone
achieves 100% accuracy.

### 5. Automated Tests
Latest reported command:
./.venv/bin/python -m pytest -q

Latest reported result:
14 passed.

### 6. Knowledge Base and Sources
The backend uses knowledge_base/agriculture.json.
Some rice-related records cite ICAR-CIWA:
https://www.ciwa.icar.gov.in/gks/crop-production

The knowledge base also contains safety-routing and capability notes.
Not every record is independently verified agricultural guidance.
Source attribution and retrieved answers must be checked against the actual
record before being presented as authoritative advice.

### 7. Speech Evaluation Status
The previous local audio files were deleted.
Therefore, a completed evaluation of 10 Hindi and 10 Kannada audio clips
has NOT been demonstrated.

To complete this requirement, collect 10 Hindi and 10 Kannada test clips,
record expected reference transcripts, run the existing speech pipeline,
and document transcription quality, language identification and latency.
Do not report this evaluation as complete until the results exist.

### 8. Multilingual Limitations
The frontend has been reported to return responses in Hindi, Kannada and English.
This does not by itself prove that every retrieved knowledge-base answer is
translated accurately into every language.

General-purpose offline translation has not been demonstrated as a complete
integrated feature. Gemini translation was explored but is not relied on for
normal chat because the user requires a free workflow without API quotas.
Browser speech synthesis depends on the voices available in the user's browser.

### 9. Remaining Validation
- Evaluate 10 Hindi and 10 Kannada audio clips.
- Add independent intent test questions, including the "other" category.
- Multilingual rice-irrigation retrieval and source attribution demonstrated in English, Hindi and Kannada; expand testing to other records and intents.
- Session continuity tested for the rice-to-irrigation example; expand to more conversations.
- Browser speech playback tested in English, Hindi and Kannada; separately validate microphone input and speech transcription.
- Record actual latency and known failure cases.
- Prepare a reproducible demonstration and final milestone checklist.

### 10. Completion Statement
The text-chat prototype, intent evaluation script and automated backend tests
are operational based on the reported results. Multilingual source attribution
has been demonstrated for a rice-irrigation example. The 10 Hindi plus 10 Kannada
audio-clip evaluation remains outstanding, as does broader end-to-end validation.
Milestone 1 should not be declared fully complete until those requirements are tested.
