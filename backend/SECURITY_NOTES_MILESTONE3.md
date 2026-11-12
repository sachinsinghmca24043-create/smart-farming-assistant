# Milestone 3 backend security notes

## Changes in this audit patch

- Admin API access now fails closed in every environment when `ADMIN_API_KEY` is missing. The previous development-mode behavior allowed admin endpoints without a credential.
- Admin key comparison remains constant-time.
- Speech transcription now requires a valid farmer bearer token, is rate-limited to 10 requests/minute per client IP, reads at most 10 MB + 1 byte, and runs CPU-heavy transcription in FastAPI's thread pool.
- Request bodies with a declared Content-Length above 12 MiB are rejected before route handling (the audio endpoint still enforces its own 10 MiB file limit).
- Baseline response security headers are applied; HSTS is enabled only when `APP_ENV=production`.
- CORS methods and allowed headers are explicit.
- Authentication and chat request schemas reject unexpected fields. Chat session IDs are validated at the schema boundary.

## Required local configuration

1. Copy `backend/.env.example` to `backend/.env`.
2. Set `ADMIN_API_KEY` to a strong random secret. The Next.js server-side admin API routes must use the same value.
3. Set `GEMINI_API_KEY` locally. Do not send or commit the key.
4. Restart the backend after changing environment variables.

## Important remaining work (not implemented by this patch)

- Replace the shared admin API-key model with role-based admin accounts and short-lived admin sessions.
- Add durable per-user/per-session token and cost accounting.
- Add Langfuse tracing and dashboard integration.
- Implement Sarvam AI STT/TTS after the project owner provisions a Sarvam API key.
- Add deployment-level body limits, HTTPS, secret management, and production origin configuration.
- Run integration/security tests in the project's actual Python environment.

This patch is a baseline hardening step, not a claim of production security certification.
