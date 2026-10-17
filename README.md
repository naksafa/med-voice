# MedVoice — talk instead of typing

MedVoice lets a doctor **dictate** a consultation instead of writing it. It turns speech into a
structured medical record and printable documents, using voice recognition plus a medical terms database:

1. **Diagnosis** — what the patient has. Conditions are detected and coded in **ICD-10**, along with symptoms.
2. **Prescription (ordonnance)** — drugs, dosage, form, frequency, duration and instructions, parsed from speech.
   It also checks **allergies and drug interactions**.
3. **Certificate (optional)** — sick leave, fitness for sport, attendance, or custom text.

Every patient gets a **unique number** (e.g. `MV-7K3P-QX9A`). At the next visit, type the number to get the
patient's full history. Prescriptions and certificates are generated as **PDF**, in French or English.

## Ordonnance & certificate PDFs
- **Your letterhead and signature on every document.** In **Doctor profile**, upload a scan or photo of your
  paper *entête* (letterhead) and of your signature/stamp. They are printed at the top and bottom of every
  ordonnance and certificate. Without them, a text header is built from your profile and a blank space is
  left to sign by hand. A PNG with a transparent background works best for the signature.
- **Preview before saving.** Click **Preview ordonnance PDF** or **Preview certificate PDF** (Prescription,
  Certificate and Review steps) to download the document exactly as it is filled in right now. Nothing is
  saved until you click **Save consultation**.

## Architecture

```
frontend/  (HTML + CSS + vanilla JS, served by the backend)
   │  microphone ─► Web Speech API (live)  or  MediaRecorder ─► /api/transcribe
   ▼
backend/  FastAPI (Python)
   ├─ speech.py         Whisper (faster-whisper), local, biased toward drug names
   ├─ medical_terms.py  knowledge base + fuzzy matcher (rapidfuzz) that fixes ASR misspellings
   ├─ nlp.py            offline rule engine: diagnosis NER, prescription parser, safety checks
   ├─ llm.py            optional Claude (Anthropic API) structuring with JSON-schema outputs
   ├─ pdf.py            ordonnance + certificate PDFs with letterhead & signature (reportlab)
   ├─ models.py         SQLite via SQLAlchemy: patients, consultations, doctor profile
   └─ data/medical_terms.json   74 conditions · 45 symptoms · 123 drugs · 18 interaction rules (FR/EN)
```

**The AI is layered.** The offline engine always works, with no internet and no API key. If Claude is
configured, it handles free-form dictation better. The output is then checked against the local
dictionary (ICD-10 codes, drug classes), and the safety checks always run locally.

## Run it

Windows:
```powershell
.\run.ps1              # first run installs everything
.\run.ps1 -Voice       # + local Whisper speech-to-text (~500 MB model download on first use)
```
macOS / Linux: `./run.sh` or `./run.sh --voice`

Then open **http://localhost:8000** in Chrome or Edge. API docs are at http://localhost:8000/docs.
If port 8000 is busy, use another one: `.un.ps1 -Port 8001`.

### Optional: Claude AI
```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
.\run.ps1
```
The model defaults to `claude-opus-5-5`. You can change it with `MEDVOICE_CLAUDE_MODEL`. Set
`MEDVOICE_DISABLE_LLM=1` to force offline mode.

### Voice engines
| Engine | Where audio goes | Notes |
|---|---|---|
| Whisper (local) | stays on this computer | needs `-Voice` install; `WHISPER_MODEL=small` (try `medium` for accuracy) |
| Browser | Google/Microsoft speech servers | live words while speaking; Chrome/Edge only |

For real patient data, use the **local Whisper** engine. The app picks it automatically when it is installed.

## Dictation tips
- Smart dictation: *"Angine aiguë avec fièvre à 39. **Ordonnance** amoxicilline 1 g deux fois par jour pendant
  6 jours, paracétamol 1 g si douleur. **Arrêt de travail** de 3 jours."* Then click **Fill the 3 parts**.
- Numbers can be spoken as words ("trois fois par jour", "for seven days").
- Everything stays editable before saving.

## Tests
```powershell
cd backend
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

## Extending the medical database
Edit `backend/data/medical_terms.json`. Add conditions (with ICD-10 code), symptoms and drugs, with French
and English aliases and brand names. Drug `tags` drive the interaction rules in `interactions`.

## Before using with real patients
This is a prototype for decision support. The doctor stays responsible for every document. To use it in
production you would still need:
- authentication for doctors, HTTPS, and encryption of the database at rest
- hosting that complies with health-data rules (e.g. HDS in France, GDPR, HIPAA)
- an audit log, backups, and a validated drug database (e.g. BDPM / Vidal) instead of the sample dataset
