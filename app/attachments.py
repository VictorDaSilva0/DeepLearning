"""Fichiers donnés directement à l'IA (sans les importer dans la bibliothèque).

Chaque pièce jointe est convertie une fois (texte Markdown, ou images pour les photos et les pages
scannées) puis gardée quelques jours dans data/attachments/<id>.json, le temps de la discussion.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import time
from pathlib import Path
from typing import Any

from . import config, importers

ATT_DIR = config.DATA_DIR / "attachments"
TTL = 3 * 86400  # s
MAX_MB = 60
MAX_IMAGES = 6  # pages scannées envoyées en images au modèle
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
ALLOWED_EXT = set(config.ALLOWED_IMPORT_EXT) | IMAGE_EXT
_ID_RE = re.compile(r"^[a-f0-9]{24}$")


def cleanup() -> None:
    """Supprime les pièces jointes de plus de quelques jours."""
    if not ATT_DIR.exists():
        return
    now = time.time()
    for p in ATT_DIR.iterdir():
        try:
            # Fichiers reçus en cours de lecture (tmp-…) : une heure suffit ; pièces jointes : quelques jours.
            if p.stat().st_mtime < now - (3600 if p.name.startswith("tmp-") else TTL):
                p.unlink()
        except OSError:
            pass


def _image_b64(path: Path) -> str:
    """Image (photo, capture d'écran) ramenée à 1600 px au plus, en PNG base64 pour le modèle."""
    import fitz  # PyMuPDF : déjà utilisé pour les PDF, il ouvre aussi les images comme une page

    original = fitz.Pixmap(str(path))
    pixels = max(original.width, original.height)
    with fitz.open(str(path)) as doc:
        page = doc[0]
        longest = max(page.rect.width, page.rect.height) or 1
        zoom = min(1600, pixels) / longest
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return base64.b64encode(pix.tobytes("png")).decode("ascii")


def create(source: Path, name: str) -> dict[str, Any]:
    """Convertit le fichier (déjà enregistré dans `source`) en pièce jointe. Lève ValueError si illisible."""
    ext = Path(name).suffix.lower()
    att_id = secrets.token_hex(12)
    text = ""
    images: list[str] = []
    warning = None
    if ext in IMAGE_EXT:
        try:
            images = [_image_b64(source)]
        except Exception as exc:
            raise ValueError(f"Image illisible : {exc}") from exc
    else:
        res = importers.import_file(source, name)
        text = res.markdown
        if res.scanned_pages and ext == ".pdf":
            for page in res.scanned_pages[:MAX_IMAGES]:
                png = importers.render_pdf_page_png(source, page, dpi=110)
                images.append(base64.b64encode(png).decode("ascii"))
            if len(res.scanned_pages) > MAX_IMAGES:
                warning = f"{len(res.scanned_pages)} pages scannées : seules les {MAX_IMAGES} premières sont lues par l'IA."
        if not text.strip() and not images:
            raise ValueError("Aucun texte lisible dans ce fichier.")
    ATT_DIR.mkdir(parents=True, exist_ok=True)
    record = {"id": att_id, "name": name, "text": text, "images": images, "created": time.time()}
    (ATT_DIR / f"{att_id}.json").write_text(json.dumps(record), encoding="utf-8")
    return public(record) | ({"warning": warning} if warning else {})


def public(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "name": record["name"],
        "chars": len(record.get("text") or ""),
        "images": len(record.get("images") or []),
    }


def load(att_id: str) -> dict[str, Any] | None:
    if not isinstance(att_id, str) or not _ID_RE.match(att_id):
        return None
    path = ATT_DIR / f"{att_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
