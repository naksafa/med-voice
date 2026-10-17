import os

os.environ["MEDVOICE_DB"] = "sqlite://"  # in-memory
os.environ["MEDVOICE_DISABLE_LLM"] = "1"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import database  # noqa: E402

database.engine = database.create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
database.SessionLocal.configure(bind=database.engine)

from app.main import app  # noqa: E402


def test_full_flow():
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"

        p = client.post("/api/patients", json={"first_name": "Amina", "last_name": "Benali",
                                               "birth_date": "1990-04-12", "allergies": "pénicilline"}).json()
        number = p["patient_number"]
        assert number.startswith("MV-")
        assert client.get("/api/patients", params={"q": "benali"}).json()[0]["patient_number"] == number

        rx = client.post("/api/ai/parse-prescription", json={
            "text": "amoxicilline 1 g 3 fois par jour pendant 6 jours", "lang": "fr", "patient_number": number}).json()
        assert rx["items"][0]["drug"] == "Amoxicilline"
        assert rx["warnings"][0]["type"] == "allergy"

        c = client.post("/api/consultations", json={
            "patient_number": number, "language": "fr", "diagnosis_text": "angine",
            "conditions": [{"name": "Angine aiguë", "code": "J03.9"}],
            "prescription": rx["items"],
            "certificate": {"type": "sick_leave", "days": 2, "start_date": "2026-10-11"},
        }).json()
        pdf = client.get(f"/api/consultations/{c['id']}/prescription.pdf")
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
        assert client.get(f"/api/consultations/{c['id']}/certificate.pdf").content.startswith(b"%PDF")
        txt = client.get(f"/api/consultations/{c['id']}/certificate.txt").json()["text"]
        assert "2 jours" in txt and "12/10/2026" in txt

        history = client.get(f"/api/patients/{number}/consultations").json()
        assert len(history) == 1 and history[0]["conditions"][0]["code"] == "J03.9"


def _png(size=(400, 120), color=(15, 118, 110, 255)) -> bytes:
    from io import BytesIO

    from PIL import Image
    buf = BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_preview_pdfs_with_letterhead_and_signature():
    with TestClient(app) as client:
        number = client.post("/api/patients", json={"first_name": "Karim", "last_name": "Haddad"}).json()["patient_number"]
        for kind in ("letterhead", "signature"):
            r = client.put(f"/api/doctor/{kind}", files={"file": (f"{kind}.png", _png(), "image/png")})
            assert r.status_code == 200 and r.json()[f"has_{kind}"]
            assert client.get(f"/api/doctor/{kind}").content.startswith(b"\x89PNG")
        assert client.put("/api/doctor/signature", files={"file": ("x.png", b"not an image", "image/png")}).status_code == 415

        draft = {"patient_number": number, "language": "fr",
                 "prescription": [{"drug": "Paracétamol", "dosage": "1 g", "frequency": "3 fois par jour"}],
                 "certificate": {"type": "sick_leave", "days": 2}}
        for kind in ("prescription", "certificate"):
            r = client.post(f"/api/preview/{kind}.pdf", json=draft)
            assert r.status_code == 200 and r.content.startswith(b"%PDF")
            assert r.headers["content-disposition"].startswith("attachment")
            assert b"/Subtype /Image" in r.content  # letterhead + signature embedded
        assert client.get("/api/patients/" + number + "/consultations").json() == []  # preview saves nothing

        assert client.post("/api/preview/certificate.pdf", json={**draft, "certificate": None}).status_code == 400
        assert client.delete("/api/doctor/signature").status_code == 204
        assert not client.get("/api/doctor").json()["has_signature"]
