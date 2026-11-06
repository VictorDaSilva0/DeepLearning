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

def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on", "oui"}


# Comptes obligatoires (outil partagé, par ex. dans Docker derrière un tunnel). Désactivé en local.
AUTH_ENABLED = _flag("DL_AUTH")
SESSION_DAYS = int(os.environ.get("DL_SESSION_DAYS", "30"))
# Premier administrateur, créé au démarrage si la base n'a encore aucun compte.
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
IN_DOCKER = _flag("DL_DOCKER")
# Tunnel Cloudflare (conteneur « tunnel ») : son serveur de métriques donne l'adresse publique.
TUNNEL_METRICS_URL = os.environ.get("TUNNEL_METRICS_URL", "").rstrip("/")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")

ALLOWED_IMPORT_EXT ={".pdf", ".docx", ".pptx", ".doc", ".ppt", ".txt", ".md", ".markdown"}
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
