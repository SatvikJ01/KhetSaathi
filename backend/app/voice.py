"""Indic speech in/out via Sarvam AI (STT: Saaras, TTS: Bulbul)."""
from __future__ import annotations

import httpx

from app.config import get_settings

STT_URL = "https://api.sarvam.ai/speech-to-text"
TTS_URL = "https://api.sarvam.ai/text-to-speech"
STT_MODEL = "saaras:v4"
TTS_MODEL = "bulbul:v3"
TTS_MAX_CHARS = 1500

# ISO 639-1 (agent output) -> Sarvam BCP-47 codes
LANG_TO_BCP47 = {
    "hi": "hi-IN", "mr": "mr-IN", "en": "en-IN", "ta": "ta-IN", "te": "te-IN", "kn": "kn-IN",
    "bn": "bn-IN", "gu": "gu-IN", "pa": "pa-IN", "ml": "ml-IN", "or": "od-IN",
}


class VoiceError(Exception):
    pass


def _headers() -> dict[str, str]:
    key = get_settings().sarvam_api_key
    if not key:
        raise VoiceError("SARVAM_API_KEY not configured")
    return {"api-subscription-key": key}


async def transcribe(audio: bytes, filename: str, content_type: str) -> dict:
    """Speech -> text with automatic language detection."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            STT_URL,
            headers=_headers(),
            files={"file": (filename, audio, content_type)},
            data={"model": STT_MODEL, "language_code": "unknown"},
        )
    if resp.status_code != 200:
        raise VoiceError(f"Sarvam STT HTTP {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    return {"transcript": body.get("transcript", ""), "language_code": body.get("language_code")}


async def synthesize(text: str, language: str) -> str:
    """Text -> base64 WAV in the farmer's language."""
    lang = LANG_TO_BCP47.get(language, "hi-IN")
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            TTS_URL,
            headers=_headers(),
            json={"text": text[:TTS_MAX_CHARS], "target_language_code": lang, "model": TTS_MODEL},
        )
    if resp.status_code != 200:
        raise VoiceError(f"Sarvam TTS HTTP {resp.status_code}: {resp.text[:200]}")
    audios = resp.json().get("audios") or []
    if not audios:
        raise VoiceError("Sarvam TTS returned no audio")
    return audios[0]
