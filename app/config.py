"""Chemins et paramètres globaux."""

from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DEEPLEARNING_DATA", ROOT_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "deeplearning.db"
STATIC_DIR = Path(__file__).resolve().parent / "static"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")

HOST = os.environ.get("DEEPLEARNING_HOST", "127.0.0.1")
PORT = int(os.environ.get("DEEPLEARNING_PORT", "8765"))

# Valeurs par défaut des réglages (modifiables depuis l'interface).
DEFAULT_SETTINGS: dict[str, object] = {
    "model": "",  # vide = premier modèle disponible
    "num_ctx": 16384,  # taille de contexte envoyée à Ollama (tokens)
    "temperature": 0.4,
    "think": False,  # mode "réflexion" pour les modèles qui le supportent
    "current_semester": "",  # ex. "S7" : semestre déplié en premier dans la bibliothèque
}

ALLOWED_IMPORT_EXT = {".pdf", ".docx", ".pptx", ".txt", ".md", ".markdown"}
MAX_UPLOAD_MB = 200


APP_DIR = Path(__file__).resolve().parent


def code_fingerprint() -> str:
    """Empreinte du code Python (date de modification la plus récente) : permet de savoir si un serveur
    déjà lancé tourne sur une ancienne version du code."""
    mtimes = [p.stat().st_mtime for p in APP_DIR.rglob("*.py") if "__pycache__" not in p.parts]
    return str(int(max(mtimes, default=0)))


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
