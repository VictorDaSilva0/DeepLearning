"""Démarrage du conteneur DeepLearning.

1. Premier démarrage : si le volume de données est vide et que la bibliothèque du PC est montée en
   lecture seule sur /seed (dossier data/ du projet), elle est recopiée dans le volume : base SQLite
   (copie cohérente, journal WAL compris) et fichiers importés.
2. Lancement du serveur (uvicorn), qui fait confiance aux en-têtes du tunnel Cloudflare (HTTPS).
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATA = Path(os.environ.get("DEEPLEARNING_DATA", "/data"))
SEED = Path(os.environ.get("DL_SEED_DIR", "/seed"))
DB_NAME = "deeplearning.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")
log = logging.getLogger("deeplearning.docker")


def import_seed() -> None:
    target = DATA / DB_NAME
    source = SEED / DB_NAME
    if target.exists() or not source.exists():
        return
    log.info("Premier démarrage : reprise de la bibliothèque existante (%s)…", SEED)
    DATA.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        # Le dossier source est en lecture seule : on copie la base et son journal WAL avant de l'ouvrir.
        for suffix in ("", "-wal", "-shm"):
            f = SEED / (DB_NAME + suffix)
            if f.exists():
                shutil.copy2(f, Path(tmp) / f.name)
        src = sqlite3.connect(Path(tmp) / DB_NAME)
        dst = sqlite3.connect(DATA / (DB_NAME + ".part"))
        src.backup(dst)
        dst.close()
        src.close()
    (DATA / (DB_NAME + ".part")).replace(target)

    uploads = SEED / "uploads"
    copied = 0
    if uploads.is_dir():
        (DATA / "uploads").mkdir(exist_ok=True)
        for f in uploads.iterdir():
            dest = DATA / "uploads" / f.name
            if f.is_file() and not dest.exists():
                shutil.copy2(f, dest)
                copied += 1
    con = sqlite3.connect(target)
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("subjects", "documents", "cards", "events")}
    con.close()
    log.info(
        "Bibliothèque reprise : %(subjects)d matières, %(documents)d documents, %(cards)d flashcards, %(events)d événements",
        counts,
    )
    log.info("%d fichiers importés recopiés.", copied)


def main() -> None:
    import_seed()
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.environ.get("DEEPLEARNING_HOST", "0.0.0.0"),
        port=int(os.environ.get("DEEPLEARNING_PORT", "8765")),
        log_level="warning",
        proxy_headers=True,  # X-Forwarded-Proto du tunnel → cookies « Secure » en HTTPS
        forwarded_allow_ips="*",  # le port n'est publié qu'en local : seuls le tunnel et le PC y accèdent
    )


if __name__ == "__main__":
    main()
