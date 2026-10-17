import secrets
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

# No 0/O/1/I so the number is easy to read out loud and type back.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_patient_number() -> str:
    """Unique patient identifier, e.g. MV-7K3P-QX9A."""
    a = "".join(secrets.choice(_ALPHABET) for _ in range(4))
    b = "".join(secrets.choice(_ALPHABET) for _ in range(4))
    return f"MV-{a}-{b}"


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_number: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100), index=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sex: Mapped[str] = mapped_column(String(10), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    allergies: Mapped[str] = mapped_column(Text, default="")
    chronic_conditions: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    consultations: Mapped[list["Consultation"]] = relationship(
        back_populates="patient", cascade="all, delete-orphan", order_by="desc(Consultation.created_at)"
    )


class Consultation(Base):
    __tablename__ = "consultations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    language: Mapped[str] = mapped_column(String(5), default="fr")

    # Part 1 — diagnosis
    diagnosis_text: Mapped[str] = mapped_column(Text, default="")
    conditions: Mapped[list] = mapped_column(JSON, default=list)  # [{name, code}]
    symptoms: Mapped[list] = mapped_column(JSON, default=list)  # [str]
    notes: Mapped[str] = mapped_column(Text, default="")

    # Part 2 — prescription (list of lines, see schemas.PrescriptionItem)
    prescription: Mapped[list] = mapped_column(JSON, default=list)
    prescription_text: Mapped[str] = mapped_column(Text, default="")

    # Part 3 — optional certificate
    certificate: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # raw dictation kept for traceability
    transcript: Mapped[str] = mapped_column(Text, default="")

    patient: Mapped[Patient] = relationship(back_populates="consultations")


class DoctorProfile(Base):
    __tablename__ = "doctor_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    specialty: Mapped[str] = mapped_column(String(120), default="")
    clinic: Mapped[str] = mapped_column(String(160), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    registration_number: Mapped[str] = mapped_column(String(60), default="")
    # PNG images printed on every document: letterhead (entête) on top, signature/stamp at the bottom
    letterhead: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    signature: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
