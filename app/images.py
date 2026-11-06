"""Images insérées dans les documents Markdown (bouton 🖼, coller, glisser-déposer).

Chaque image est vérifiée, redressée (photos de téléphone), réduite à une taille raisonnable puis
enregistrée dans data/images/<id>.<ext>. Le Markdown y fait référence : ![légende](/api/images/<id>.<ext>).
"""

from __future__ import annotations

import io
import re
import secrets
from typing import Any

from . import config

IMG_DIR = config.DATA_DIR / "images"
MAX_MB = 20
MAX_SIDE = 2000  # px : largement assez pour un écran, et léger à charger sur téléphone
NAME_RE = re.compile(r"^[a-f0-9]{24}\.(png|jpg|gif)$")
MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif"}
_FORMATS = {"PNG", "JPEG", "MPO", "GIF", "WEBP", "BMP"}  # pas de SVG : il peut contenir du script


def save(data: bytes) -> dict[str, Any]:
    """Enregistre l'image ; lève ValueError si ce n'est pas une image acceptée."""
    from PIL import Image, ImageOps  # Pillow (déjà installé avec python-pptx)

    try:
        probe = Image.open(io.BytesIO(data))
        probe.verify()
        img = Image.open(io.BytesIO(data))
        fmt = img.format
    except Exception as exc:
        raise ValueError("Ce fichier n'est pas une image lisible (PNG, JPEG, GIF ou WebP).") from exc
    if fmt not in _FORMATS:
        raise ValueError(f"Format d'image non pris en charge ({fmt}) : utilise PNG, JPEG, GIF ou WebP.")

    img_id = secrets.token_hex(12)
    if fmt == "GIF" and len(data) <= 8 * 1024 * 1024:
        ext, out = "gif", data  # gardé tel quel (animations)
        width, height = img.size
    else:
        img = ImageOps.exif_transpose(img)  # photo prise en portrait : remise à l'endroit
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        width, height = img.size
        buf = io.BytesIO()
        transparent = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        if fmt == "PNG" or transparent:
            # Captures d'écran, schémas : PNG, net et sans perte.
            ext = "png"
            img.save(buf, "PNG", optimize=True)
        else:
            ext = "jpg"
            img.convert("RGB").save(buf, "JPEG", quality=85, optimize=True, progressive=True)
        out = buf.getvalue()

    IMG_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{img_id}.{ext}"
    (IMG_DIR / name).write_bytes(out)
    return {"url": f"/api/images/{name}", "width": width, "height": height, "size": len(out)}
