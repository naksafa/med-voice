from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Consultation, Patient, new_patient_number
from ..schemas import ConsultationOut, PatientCreate, PatientOut, PatientUpdate

router = APIRouter(prefix="/api/patients", tags=["patients"])


def get_patient_or_404(db: Session, number: str) -> Patient:
    patient = db.scalar(select(Patient).where(Patient.patient_number == number.strip().upper()))
    if not patient:
        raise HTTPException(404, f"Patient {number} not found")
    return patient


def _out(p: Patient) -> PatientOut:
    return PatientOut.model_validate(p).model_copy(update={"consultation_count": len(p.consultations)})


@router.post("", response_model=PatientOut, status_code=201)
def create_patient(data: PatientCreate, db: Session = Depends(get_db)):
    number = new_patient_number()
    while db.scalar(select(Patient.id).where(Patient.patient_number == number)):
        number = new_patient_number()
    patient = Patient(patient_number=number, **data.model_dump())
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return _out(patient)


@router.get("", response_model=list[PatientOut])
def search_patients(q: str = "", limit: int = 20, db: Session = Depends(get_db)):
    stmt = select(Patient).order_by(Patient.created_at.desc()).limit(min(limit, 100))
    if q.strip():
        like = f"%{q.strip().lower()}%"
        full = func.lower(Patient.first_name + " " + Patient.last_name)
        full_rev = func.lower(Patient.last_name + " " + Patient.first_name)
        stmt = stmt.where(or_(func.upper(Patient.patient_number).like(f"%{q.strip().upper()}%"),
                              full.like(like), full_rev.like(like), Patient.phone.like(like)))
    return [_out(p) for p in db.scalars(stmt)]


@router.get("/{number}", response_model=PatientOut)
def get_patient(number: str, db: Session = Depends(get_db)):
    return _out(get_patient_or_404(db, number))


@router.put("/{number}", response_model=PatientOut)
def update_patient(number: str, data: PatientUpdate, db: Session = Depends(get_db)):
    patient = get_patient_or_404(db, number)
    for k, v in data.model_dump().items():
        setattr(patient, k, v)
    db.commit()
    return _out(patient)


@router.get("/{number}/consultations", response_model=list[ConsultationOut])
def patient_history(number: str, db: Session = Depends(get_db)):
    patient = get_patient_or_404(db, number)
    rows = db.scalars(select(Consultation).where(Consultation.patient_id == patient.id)
                      .order_by(Consultation.created_at.desc()))
    return [ConsultationOut.model_validate(c).model_copy(update={"patient_number": patient.patient_number}) for c in rows]
