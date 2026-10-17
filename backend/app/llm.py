"""Optional Claude-powered structuring of free dictation.

Enabled when an Anthropic credential is configured (ANTHROPIC_API_KEY, or
MEDVOICE_USE_CLAUDE=1 with an `ant auth login` profile). If it is not
configured, or any call fails, the caller falls back to the offline rule
engine in nlp.py, so the app always works.
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("medvoice.llm")

MODEL = os.getenv("MEDVOICE_CLAUDE_MODEL", "claude-opus-5-5")

SYSTEM_PROMPT = """You are a clinical documentation assistant inside MedVoice, a dictation tool used by physicians.
The physician dictates by voice; the transcript may contain speech-recognition errors, missing punctuation,
and numbers written as words. Your job is to turn the dictation into a structured record.

Rules:
- Only record what the physician actually said. Never invent diagnoses, drugs, doses or durations;
  leave a field as an empty string when it was not dictated.
- Fix obvious speech-recognition misspellings of drug and disease names (e.g. "amoxiciline" -> "Amoxicilline").
  Candidate terms found by a local medical dictionary are provided as hints; prefer them when they fit.
- Write every field in the requested output language, using standard clinical wording.
- Give an ICD-10 code for each condition when you are confident, otherwise an empty string.
- Prescription lines: one entry per drug, with dosage (e.g. "500 mg"), pharmaceutical form, quantity per intake,
  frequency, duration and any instructions (e.g. "after meals").
- Certificate: set needed=true only if the physician asked for a certificate (sick leave, fitness for sport,
  attendance, or other). start_date in YYYY-MM-DD if stated, else empty."""

_ITEM = {
    "type": "object",
    "properties": {
        "drug": {"type": "string"},
        "dosage": {"type": "string"},
        "form": {"type": "string"},
        "quantity": {"type": "string"},
        "frequency": {"type": "string"},
        "duration": {"type": "string"},
        "instructions": {"type": "string"},
    },
    "required": ["drug", "dosage", "form", "quantity", "frequency", "duration", "instructions"],
    "additionalProperties": False,
}

SCHEMA = {
    "type": "object",
    "properties": {
        "diagnosis": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "conditions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}, "code": {"type": "string"}},
                        "required": ["name", "code"],
                        "additionalProperties": False,
                    },
                },
                "symptoms": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string"},
            },
            "required": ["summary", "conditions", "symptoms", "notes"],
            "additionalProperties": False,
        },
        "prescription": {"type": "array", "items": _ITEM},
        "certificate": {
            "type": "object",
            "properties": {
                "needed": {"type": "boolean"},
                "type": {"type": "string", "enum": ["sick_leave", "fitness", "attendance", "custom", "none"]},
                "days": {"type": "integer"},
                "start_date": {"type": "string"},
                "sport": {"type": "string"},
                "remarks": {"type": "string"},
            },
            "required": ["needed", "type", "days", "start_date", "sport", "remarks"],
            "additionalProperties": False,
        },
    },
    "required": ["diagnosis", "prescription", "certificate"],
    "additionalProperties": False,
}

SECTION_HINT = {
    "all": "The dictation may cover diagnosis, prescription and certificate.",
    "diagnosis": "This dictation is ONLY the diagnosis / clinical findings part. Leave prescription empty and certificate.needed=false unless explicitly dictated.",
    "prescription": "This dictation is ONLY the prescription part. Put drugs in prescription; leave diagnosis fields empty.",
}


def is_available() -> bool:
    if os.getenv("MEDVOICE_DISABLE_LLM"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("MEDVOICE_USE_CLAUDE"))


def structure(text: str, lang: str, section: str = "all", hints: list[str] | None = None) -> dict | None:
    """Return the structured dict, or None if Claude is unavailable / declined / failed."""
    if not is_available() or not text.strip():
        return None
    import anthropic

    language = "French" if lang == "fr" else "English"
    user = (
        f"Output language: {language}\n"
        f"{SECTION_HINT.get(section, SECTION_HINT['all'])}\n"
        f"Dictionary hints: {', '.join(hints or []) or 'none'}\n\n"
        f"<dictation>\n{text}\n</dictation>"
    )
    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            # if the request is declined by a safety classifier, the API retries it on a fallback model
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.RateLimitError as e:
        log.warning("Claude rate limited, using rule engine: %s", e)
        return None
    except anthropic.APIStatusError as e:
        log.warning("Claude API error %s, using rule engine: %s", e.status_code, e.message)
        return None
    except anthropic.APIConnectionError as e:
        log.warning("Claude unreachable, using rule engine: %s", e)
        return None

    if response.stop_reason in ("refusal", "max_tokens"):
        log.warning("Claude stop_reason=%s, using rule engine", response.stop_reason)
        return None
    text_block = next((b for b in response.content if b.type == "text"), None)
    if text_block is None:
        return None
    return json.loads(text_block.text)
