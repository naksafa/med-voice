import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import llm, nlp, speech
from ..database import get_db
from ..medical_terms import get_kb
from ..schemas import SafetyIn, TextIn
from .patients import get_patient_or_404

log = logging.getLogger("medvoice.ai")
router = APIRouter(prefix="/api", tags=["ai"])
MAX_AUDIO_BYTES = 25 * 1024 * 1024


@router.get("/health")
def health():
    return {"status": "ok", "whisper": speech.is_available(), "llm": llm.is_available(),
            "llm_model": llm.MODEL if llm.is_available() else None, "knowledge_base": get_kb().stats()}


@router.get("/terms/search")
def search_terms(q: str, lang: str = "fr", type: str | None = None, limit: int = 10):
    return get_kb().search(q, lang, type, min(limit, 30))


def _allergies(db: Session, number: str | None) -> str:
    if not number:
        return ""
    try:
        return get_patient_or_404(db, number).allergies or ""
    except HTTPException:
        return ""


def _hints(text: str, lang: str) -> list[str]:
    return sorted({m.entity.name(lang) for m in get_kb().find(text, lang)})


def _ground_items(items: list[dict], lang: str) -> list[dict]:
    """Attach therapeutic class and canonical spelling from the local knowledge base."""
    kb = get_kb()
    out = []
    for it in items:
        it = {k: (v or "") for k, v in it.items()}
        if not it.get("drug", "").strip():
            continue
        ent = kb.lookup_medication(it["drug"], lang)
        if ent:
            it.setdefault("drug_class", ent.drug_class or "")
            it["drug_class"] = it["drug_class"] or ent.drug_class or ""
            if len(it["drug"].split()) <= 2:
                it["drug"] = ent.name(lang)
        it.setdefault("drug_class", "")
        out.append(it)
    return out


def _ground_conditions(conditions: list[dict], lang: str) -> list[dict]:
    kb = get_kb()
    out = []
    for c in conditions:
        if not c.get("name"):
            continue
        ent = kb.lookup_condition(c["name"], lang)
        out.append({"type": "condition", "name": c["name"], "code": c.get("code") or (ent.code if ent else ""),
                    "source": "ai"})
    return out


@router.post("/ai/analyze-diagnosis")
def analyze_diagnosis(body: TextIn):
    result = nlp.analyze_diagnosis(body.text, body.lang)
    if body.use_ai:
        ai = _safe_llm(body.text, body.lang, "diagnosis")
        if ai:
            d = ai["diagnosis"]
            result.update(engine="claude", summary=d["summary"], notes=d["notes"],
                          conditions=_ground_conditions(d["conditions"], body.lang) or result["conditions"],
                          symptoms=[{"type": "symptom", "name": s} for s in d["symptoms"]] or result["symptoms"])
    return result


@router.post("/ai/parse-prescription")
def parse_prescription(body: TextIn, db: Session = Depends(get_db)):
    items, engine = None, "rules"
    if body.use_ai:
        ai = _safe_llm(body.text, body.lang, "prescription")
        if ai and ai["prescription"]:
            items, engine = _ground_items(ai["prescription"], body.lang), "claude"
    if items is None:
        items = nlp.parse_prescription(body.text, body.lang)
    warnings = nlp.check_safety(items, _allergies(db, body.patient_number), body.lang)
    return {"engine": engine, "items": items, "warnings": warnings}


@router.post("/ai/safety-check")
def safety_check(body: SafetyIn, db: Session = Depends(get_db)):
    items = [i.model_dump() for i in body.items]
    return {"warnings": nlp.check_safety(items, _allergies(db, body.patient_number), body.lang)}


@router.post("/ai/structure")
def structure(body: TextIn, db: Session = Depends(get_db)):
    """One dictation for the whole consultation -> diagnosis + prescription + certificate."""
    result = None
    if body.use_ai:
        ai = _safe_llm(body.text, body.lang, "all")
        if ai:
            cert = ai["certificate"]
            result = {
                "engine": "claude",
                "diagnosis": {**ai["diagnosis"], "conditions": _ground_conditions(ai["diagnosis"]["conditions"], body.lang)},
                "prescription": _ground_items(ai["prescription"], body.lang),
                "certificate": None if not cert["needed"] or cert["type"] == "none" else
                {k: v for k, v in cert.items() if k != "needed"},
            }
    if result is None:
        result = nlp.structure_rules(body.text, body.lang)
    result["warnings"] = nlp.check_safety(result["prescription"], _allergies(db, body.patient_number), body.lang)
    return result


def _safe_llm(text: str, lang: str, section: str) -> dict | None:
    try:
        return llm.structure(text, lang, section, _hints(text, lang))
    except Exception:  # never let the optional AI layer break the dictation flow
        log.exception("Claude structuring failed; falling back to rules")
        return None


@router.post("/transcribe")
async def transcribe(file: UploadFile = File(...), lang: str = Form("fr")):
    if not speech.is_available():
        raise HTTPException(501, "Server-side Whisper is not installed (pip install -r requirements-voice.txt)")
    audio = await file.read()
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audio file too large")
    suffix = Path(file.filename or "audio.webm").suffix or ".webm"
    from starlette.concurrency import run_in_threadpool
    result = await run_in_threadpool(speech.transcribe, audio, lang, suffix)
    corrected, corrections = get_kb().correct_text(result["text"], lang)
    return result | {"corrected_text": corrected, "corrections": corrections}
