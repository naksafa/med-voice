from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Lang = Literal["fr", "en"]


class PatientBase(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    birth_date: date | None = None
    sex: str = ""
    phone: str = ""
    allergies: str = ""
    chronic_conditions: str = ""
    notes: str = ""


class PatientCreate(PatientBase):
    pass


class PatientUpdate(PatientBase):
    pass


class ConsultationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    conditions: list[dict]
    prescription: list[dict]
    certificate: dict | None


class PatientOut(PatientBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patient_number: str
    created_at: datetime
    consultation_count: int = 0


class PrescriptionItem(BaseModel):
    drug: str
    drug_class: str = ""
    dosage: str = ""
    form: str = ""
    quantity: str = ""
    frequency: str = ""
    duration: str = ""
    instructions: str = ""


class Certificate(BaseModel):
    type: Literal["sick_leave", "fitness", "attendance", "custom"] = "sick_leave"
    days: int = Field(default=0, ge=0, le=365)
    start_date: date | None = None
    sport: str = ""
    remarks: str = ""


class ConsultationIn(BaseModel):
    patient_number: str
    language: Lang = "fr"
    diagnosis_text: str = ""
    conditions: list[dict] = []
    symptoms: list[str] = []
    notes: str = ""
    prescription: list[PrescriptionItem] = []
    prescription_text: str = ""
    certificate: Certificate | None = None
    transcript: str = ""


class ConsultationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    language: str
    diagnosis_text: str
    conditions: list[dict]
    symptoms: list[str]
    notes: str
    prescription: list[dict]
    prescription_text: str
    certificate: dict | None
    transcript: str
    patient_number: str = ""


class DoctorProfileIO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str = ""
    specialty: str = ""
    clinic: str = ""
    address: str = ""
    phone: str = ""
    registration_number: str = ""


class DoctorProfileOut(DoctorProfileIO):
    has_letterhead: bool = False
    has_signature: bool = False


class TextIn(BaseModel):
    text: str = Field(max_length=20000)
    lang: Lang = "fr"
    use_ai: bool = True
    patient_number: str | None = None


class SafetyIn(BaseModel):
    items: list[PrescriptionItem]
    lang: Lang = "fr"
    patient_number: str | None = None
