"""PDF generation for prescriptions (ordonnances) and medical certificates."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import HRFlowable, Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import Consultation, DoctorProfile, Patient

ACCENT = colors.HexColor("#0f766e")

T = {
    "fr": {
        "prescription": "ORDONNANCE", "certificate": "CERTIFICAT MÉDICAL", "patient": "Patient",
        "number": "N° patient", "born": "Né(e) le", "age": "Âge", "years": "ans", "date": "Date",
        "signature": "Signature et cachet du médecin", "dosage": "Dosage", "freq": "Posologie",
        "duration": "Durée", "instructions": "Consignes", "made_at": "Fait le", "reg": "N° RPPS / Ordre",
        "footer": "Document généré avec MedVoice", "dr": "Dr",
    },
    "en": {
        "prescription": "PRESCRIPTION", "certificate": "MEDICAL CERTIFICATE", "patient": "Patient",
        "number": "Patient no.", "born": "Born", "age": "Age", "years": "years", "date": "Date",
        "signature": "Physician's signature and stamp", "dosage": "Dosage", "freq": "Frequency",
        "duration": "Duration", "instructions": "Instructions", "made_at": "Issued on", "reg": "Registration no.",
        "footer": "Generated with MedVoice", "dr": "Dr",
    },
}


def _styles():
    ss = getSampleStyleSheet()
    return {
        "h": ParagraphStyle("h", parent=ss["Title"], fontSize=18, textColor=ACCENT, spaceAfter=4, alignment=1),
        "doc": ParagraphStyle("doc", parent=ss["Normal"], fontSize=11, leading=14, fontName="Helvetica-Bold"),
        "small": ParagraphStyle("small", parent=ss["Normal"], fontSize=9, leading=12, textColor=colors.HexColor("#475569")),
        "body": ParagraphStyle("body", parent=ss["Normal"], fontSize=11, leading=16),
        "drug": ParagraphStyle("drug", parent=ss["Normal"], fontSize=12, leading=15, fontName="Helvetica-Bold"),
        "right": ParagraphStyle("right", parent=ss["Normal"], fontSize=10, alignment=2),
    }


def _fmt_date(d: date | datetime | None, lang: str) -> str:
    if not d:
        return ""
    return d.strftime("%d/%m/%Y") if lang == "fr" else d.strftime("%B %d, %Y")


def _age(birth: date | None) -> int | None:
    if not birth:
        return None
    today = date.today()
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def _image(data: bytes, max_w: float, max_h: float, align: str) -> Image:
    iw, ih = ImageReader(BytesIO(data)).getSize()
    scale = min(max_w / iw, max_h / ih)
    img = Image(BytesIO(data), width=iw * scale, height=ih * scale)
    img.hAlign = align
    return img


def _header(story, s, t, doctor: DoctorProfile | None, patient: Patient, when: datetime, lang: str):
    d = doctor or DoctorProfile()
    if d.letterhead:
        # the doctor's own entête replaces the text header
        story += [_image(d.letterhead, 170 * mm, 45 * mm, "CENTER"), Spacer(1, 3 * mm),
                  Paragraph(escape(f"{t['date']} : {_fmt_date(when, lang)}"), s["right"]), Spacer(1, 3 * mm)]
        _patient_line(story, s, t, patient, lang)
        return
    left = [Paragraph(escape(f"{t['dr']} {d.name}" if d.name else t["dr"] + " ……………………"), s["doc"])]
    for line in (d.specialty, d.clinic, d.address, d.phone):
        if line:
            left.append(Paragraph(escape(line).replace("\n", "<br/>"), s["small"]))
    if d.registration_number:
        left.append(Paragraph(escape(f"{t['reg']} : {d.registration_number}"), s["small"]))
    right = [Paragraph(escape(f"{t['date']} : {_fmt_date(when, lang)}"), s["right"])]
    tbl = Table([[left, right]], colWidths=[110 * mm, 60 * mm])
    tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [tbl, Spacer(1, 4 * mm), HRFlowable(width="100%", color=ACCENT, thickness=1.2), Spacer(1, 4 * mm)]
    _patient_line(story, s, t, patient, lang)


def _patient_line(story, s, t, patient: Patient, lang: str):
    info = f"<b>{t['patient']} :</b> {escape(patient.last_name.upper())} {escape(patient.first_name)}"
    age = _age(patient.birth_date)
    if patient.birth_date:
        info += f" &nbsp;·&nbsp; {t['born']} {_fmt_date(patient.birth_date, lang)}"
    if age is not None:
        info += f" ({age} {t['years']})"
    info += f" &nbsp;·&nbsp; <b>{t['number']} :</b> {escape(patient.patient_number)}"
    story += [Paragraph(info, s["body"]), Spacer(1, 6 * mm)]


def _signature(story, s, t, doctor: DoctorProfile | None):
    story += [Spacer(1, 12 * mm), Paragraph(t["signature"], s["right"])]
    if doctor and doctor.signature:
        story += [Spacer(1, 2 * mm), _image(doctor.signature, 60 * mm, 32 * mm, "RIGHT"), Spacer(1, 6 * mm)]
    else:
        story.append(Spacer(1, 20 * mm))


def _build(story) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                            title="MedVoice", author="MedVoice")
    doc.build(story)
    return buf.getvalue()


def prescription_pdf(c: Consultation, doctor: DoctorProfile | None) -> bytes:
    lang = c.language if c.language in T else "fr"
    t, s = T[lang], _styles()
    story = []
    _header(story, s, t, doctor, c.patient, c.created_at, lang)
    story += [Paragraph(t["prescription"], s["h"]), Spacer(1, 6 * mm)]
    for i, item in enumerate(c.prescription or [], 1):
        title = escape(item.get("drug", ""))
        if item.get("dosage"):
            title += f" {escape(item['dosage'])}"
        if item.get("form"):
            title += f" — <font size=10>{escape(item['form'])}</font>"
        story.append(Paragraph(f"{i}. {title}", s["drug"]))
        line = " · ".join(escape(x) for x in (item.get("quantity"), item.get("frequency")) if x)
        if item.get("duration"):
            line += (" · " if line else "") + f"{t['duration']} : {escape(item['duration'])}"
        if line:
            story.append(Paragraph(line, s["body"]))
        if item.get("instructions"):
            story.append(Paragraph(f"<i>{escape(item['instructions'])}</i>", s["body"]))
        story.append(Spacer(1, 5 * mm))
    _signature(story, s, t, doctor)
    story.append(Paragraph(t["footer"], s["small"]))
    return _build(story)


def certificate_text(c: Consultation, doctor: DoctorProfile | None) -> str:
    cert = c.certificate or {}
    lang = c.language if c.language in T else "fr"
    p, d = c.patient, doctor or DoctorProfile()
    dr = d.name or "……………………"
    name = f"{p.first_name} {p.last_name.upper()}"
    born = f", né(e) le {_fmt_date(p.birth_date, 'fr')}" if lang == "fr" and p.birth_date else (
        f", born {_fmt_date(p.birth_date, 'en')}" if p.birth_date else "")
    seen = _fmt_date(c.created_at, lang)
    days = int(cert.get("days") or 0)
    start = date.fromisoformat(cert["start_date"]) if cert.get("start_date") else c.created_at.date()
    end = start + timedelta(days=max(days - 1, 0))
    kind = cert.get("type", "custom")

    if lang == "fr":
        intro = f"Je soussigné(e), Dr {dr}, certifie avoir examiné ce jour, le {seen}, {name}{born}."
        body = {
            "sick_leave": f"Son état de santé nécessite un arrêt de travail de {days} jour{'s' if days > 1 else ''}, "
                          f"du {_fmt_date(start, 'fr')} au {_fmt_date(end, 'fr')} inclus.",
            "fitness": "L'examen clinique ne révèle pas de contre-indication apparente à la pratique "
                       f"{('du ' + cert['sport']) if cert.get('sport') else 'du sport'}, y compris en compétition.",
            "attendance": "Le/la patient(e) s'est présenté(e) en consultation médicale ce jour.",
            "custom": "",
        }[kind]
        close = "Certificat établi à la demande de l'intéressé(e) et remis en main propre pour faire valoir ce que de droit."
    else:
        intro = f"I, the undersigned, Dr {dr}, certify that I examined {name}{born} today, {seen}."
        body = {
            "sick_leave": f"Their state of health requires sick leave from work for {days} day{'s' if days > 1 else ''}, "
                          f"from {_fmt_date(start, 'en')} to {_fmt_date(end, 'en')} inclusive.",
            "fitness": "The clinical examination shows no apparent contraindication to the practice of "
                       f"{cert.get('sport') or 'sport'}, including in competition.",
            "attendance": "The patient attended a medical consultation today.",
            "custom": "",
        }[kind]
        close = "This certificate is issued at the patient's request and handed to them in person."
    parts = [intro, body]
    if cert.get("remarks") and kind != "custom":
        parts.append(cert["remarks"])
    elif kind == "custom":
        parts.append(cert.get("remarks", ""))
    parts.append(close)
    return "\n\n".join(x for x in parts if x)


def certificate_pdf(c: Consultation, doctor: DoctorProfile | None) -> bytes:
    lang = c.language if c.language in T else "fr"
    t, s = T[lang], _styles()
    story = []
    _header(story, s, t, doctor, c.patient, c.created_at, lang)
    story += [Paragraph(t["certificate"], s["h"]), Spacer(1, 8 * mm)]
    for para in certificate_text(c, doctor).split("\n\n"):
        story += [Paragraph(escape(para), s["body"]), Spacer(1, 4 * mm)]
    story.append(Paragraph(f"{t['made_at']} {_fmt_date(c.created_at, lang)}", s["body"]))
    _signature(story, s, t, doctor)
    story.append(Paragraph(t["footer"], s["small"]))
    return _build(story)
