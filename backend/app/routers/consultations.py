from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Consultation, DoctorProfile
from ..pdf import certificate_pdf, certificate_text, prescription_pdf
from ..schemas import ConsultationIn, ConsultationOut, DoctorProfileIO, DoctorProfileOut
from .patients import get_patient_or_404

router = APIRouter(prefix="/api", tags=["consultations"])
MAX_IMAGE_BYTES = 8 * 1024 * 1024
ImageKind = Literal["letterhead", "signature"]


def _get(db: Session, cid: int) -> Consultation:
    c = db.get(Consultation, cid)
    if not c:
        raise HTTPException(404, "Consultation not found")
    return c


def _out(c: Consultation) -> ConsultationOut:
    return ConsultationOut.model_validate(c).model_copy(update={"patient_number": c.patient.patient_number})


def _apply(c: Consultation, data: ConsultationIn):
    c.language = data.language
    c.diagnosis_text = data.diagnosis_text
    c.conditions = data.conditions
    c.symptoms = data.symptoms
    c.notes = data.notes
    c.prescription = [i.model_dump() for i in data.prescription if i.drug.strip()]
    c.prescription_text = data.prescription_text
    c.certificate = data.certificate.model_dump(mode="json") if data.certificate else None
    c.transcript = data.transcript


@router.post("/consultations", response_model=ConsultationOut, status_code=201)
def create_consultation(data: ConsultationIn, db: Session = Depends(get_db)):
    patient = get_patient_or_404(db, data.patient_number)
    c = Consultation(patient=patient)
    _apply(c, data)
    db.add(c)
    db.commit()
    db.refresh(c)
    return _out(c)


@router.get("/consultations/{cid}", response_model=ConsultationOut)
def get_consultation(cid: int, db: Session = Depends(get_db)):
    return _out(_get(db, cid))


@router.put("/consultations/{cid}", response_model=ConsultationOut)
def update_consultation(cid: int, data: ConsultationIn, db: Session = Depends(get_db)):
    c = _get(db, cid)
    _apply(c, data)
    db.commit()
    return _out(c)


@router.delete("/consultations/{cid}", status_code=204)
def delete_consultation(cid: int, db: Session = Depends(get_db)):
    db.delete(_get(db, cid))
    db.commit()


def _doctor(db: Session) -> DoctorProfile | None:
    return db.scalar(select(DoctorProfile).limit(1))


def _pdf(c, kind: str, doctor: DoctorProfile | None, download: bool = False) -> Response:
    missing = 400 if download else 404  # a draft with nothing to print is a bad request, not a missing resource
    if kind == "prescription":
        if not c.prescription:
            raise HTTPException(missing, "This consultation has no prescription")
        data, prefix = prescription_pdf(c, doctor), "ordonnance"
    else:
        if not c.certificate:
            raise HTTPException(missing, "This consultation has no certificate")
        data, prefix = certificate_pdf(c, doctor), "certificat"
    name = f"{prefix}_{c.patient.patient_number}_{c.created_at:%Y%m%d}.pdf"
    disposition = "attachment" if download else "inline"
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'{disposition}; filename="{name}"'})


@router.get("/consultations/{cid}/prescription.pdf")
def get_prescription_pdf(cid: int, db: Session = Depends(get_db)):
    return _pdf(_get(db, cid), "prescription", _doctor(db))


@router.get("/consultations/{cid}/certificate.pdf")
def get_certificate_pdf(cid: int, db: Session = Depends(get_db)):
    return _pdf(_get(db, cid), "certificate", _doctor(db))


@router.post("/preview/{kind}.pdf")
def preview_pdf(kind: Literal["prescription", "certificate"], data: ConsultationIn, db: Session = Depends(get_db)):
    """PDF of the consultation as currently filled in, without saving it."""
    draft = SimpleNamespace(patient=get_patient_or_404(db, data.patient_number), created_at=datetime.now())
    _apply(draft, data)
    return _pdf(draft, kind, _doctor(db), download=True)


@router.get("/consultations/{cid}/certificate.txt")
def get_certificate_text(cid: int, db: Session = Depends(get_db)):
    c = _get(db, cid)
    if not c.certificate:
        raise HTTPException(404, "This consultation has no certificate")
    return {"text": certificate_text(c, _doctor(db))}


def _doctor_out(d: DoctorProfile | None) -> DoctorProfileOut:
    if not d:
        return DoctorProfileOut()
    return DoctorProfileOut(**DoctorProfileIO.model_validate(d).model_dump(),
                            has_letterhead=bool(d.letterhead), has_signature=bool(d.signature))


@router.get("/doctor", response_model=DoctorProfileOut)
def get_doctor(db: Session = Depends(get_db)):
    return _doctor_out(_doctor(db))


@router.put("/doctor", response_model=DoctorProfileOut)
def save_doctor(data: DoctorProfileIO, db: Session = Depends(get_db)):
    d = _doctor(db) or DoctorProfile()
    for k, v in data.model_dump().items():
        setattr(d, k, v)
    db.add(d)
    db.commit()
    return _doctor_out(d)


@router.put("/doctor/{kind}", response_model=DoctorProfileOut)
def upload_doctor_image(kind: ImageKind, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Letterhead (entête) or signature/stamp image, stored as PNG and printed on every document."""
    raw = file.file.read(MAX_IMAGE_BYTES + 1)
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image too large (max 8 MB)")
    try:
        img = Image.open(BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError):
        raise HTTPException(415, "Not an image — use PNG or JPG")
    img = img.convert("RGBA" if "A" in img.getbands() or img.mode == "P" else "RGB")
    img.thumbnail((2400, 2400))  # keeps PDFs light
    out = BytesIO()
    img.save(out, "PNG", optimize=True)
    d = _doctor(db) or DoctorProfile()
    setattr(d, kind, out.getvalue())
    db.add(d)
    db.commit()
    return _doctor_out(d)


@router.get("/doctor/{kind}")
def get_doctor_image(kind: ImageKind, db: Session = Depends(get_db)):
    data = getattr(_doctor(db), kind, None)
    if not data:
        raise HTTPException(404, f"No {kind} uploaded")
    return Response(data, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.delete("/doctor/{kind}", status_code=204)
def delete_doctor_image(kind: ImageKind, db: Session = Depends(get_db)):
    d = _doctor(db)
    if d:
        setattr(d, kind, None)
        db.commit()
