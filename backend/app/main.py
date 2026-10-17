"""MedVoice — voice-driven medical documentation.

Run from the backend/ folder:  uvicorn app.main:app --reload
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .database import init_db
from .medical_terms import get_kb
from .routers import ai, consultations, patients

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    get_kb()  # load the medical dictionary once at startup
    yield


app = FastAPI(title="MedVoice API", version="1.0.0", lifespan=lifespan)
app.include_router(patients.router)
app.include_router(consultations.router)
app.include_router(ai.router)

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
