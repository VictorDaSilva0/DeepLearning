"""API FastAPI + service de l'interface web."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import unicodedata
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi.requests import HTTPConnection
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, attachments, auth, config, db, importers, live, llm, office, prompts, render, srs

log = logging.getLogger("deeplearning")
LOADED_CODE = config.code_fingerprint()  # version du code chargée par ce processus

# API accessible sans être connecté (le reste de /api/ exige une session quand DL_AUTH=1).
# /api/live (temps réel) vérifie la session lui-même.
PUBLIC_API = {"/api/version", "/api/auth/login", "/api/auth/me", "/api/live"}
# Avec un mot de passe provisoire, seules ces routes restent ouvertes jusqu'au changement.
PROVISIONAL_API = {"/api/auth/password", "/api/auth/logout"}


def require_login(request: HTTPConnection) -> None:
    """Dépendance appliquée à toutes les routes : vérifie la session pour l'API."""
    path = request.url.path
    if not path.startswith("/api/") or path in PUBLIC_API:
        return
    user = auth.current_user(request)
    if user is None:
        raise HTTPException(401, "Connexion requise.")
    if user.get("must_change") and path not in PROVISIONAL_API:
        raise HTTPException(403, "Choisis d'abord un nouveau mot de passe.")
    request.state.user = user


async def remember_user(request: HTTPConnection) -> None:
    """Auteur de la requête, pour annoncer ses modifications aux autres (« Léa a importé… »)."""
    live.CURRENT_USER.set(getattr(request.state, "user", None))


def require_admin(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None) or auth.current_user(request)
    if not user or not user.get("is_admin"):
        raise HTTPException(403, "Réservé aux administrateurs.")
    return user


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    auth.bootstrap_admin()
    yield
    await live.shutdown()


app = FastAPI(
    title="DeepLearning", version=__version__, lifespan=lifespan,
    dependencies=[Depends(require_login), Depends(remember_user)],
)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")

KINDS = {
    "cours", "td", "tp", "note", "fiche", "resume", "exercices", "qcm", "flashcards",
    "explication", "glossaire", "plan", "questions", "corrige", "rendu", "relecture", "annale", "examen",
}
# cours = CM ; rendu = compte-rendu de TD/TP ; annale = contrôle d'une année précédente ;
# examen = examen d'entraînement (rédigé par l'IA, ou ajouté à la main)
SOURCE_KINDS = {"cours", "td", "tp", "annale", "examen", "rendu"}


def _number_in(text: str, prefix: str) -> str | None:
    m = re.search(rf"(?:^|[^a-z]){prefix}\s*[-_]?\s*(\d+)", text or "", re.IGNORECASE)
    return m.group(1) if m else None


def _link_report(doc: dict[str, Any]) -> dict[str, Any]:
    """Rattache un compte-rendu au sujet de TD/TP correspondant de la même matière (même numéro, ou seul sujet)."""
    if doc["kind"] != "rendu" or doc.get("parent_id") or doc.get("subject_id") is None:
        return doc
    subjects = [d for d in db.list_documents(doc["subject_id"]) if d["kind"] in ("td", "tp") and d["id"] != doc["id"]]
    if not subjects:
        return doc
    name = f"{doc['title']} {doc.get('source_name') or ''}"
    for prefix in ("tp", "td"):
        num = _number_in(name, prefix)
        if num:
            same = [d for d in subjects if d["kind"] == prefix and _number_in(f"{d['title']} {d.get('source_name') or ''}", prefix) == num]
            if len(same) == 1:
                return db.update_document(doc["id"], parent_id=same[0]["id"]) or doc
    if len(subjects) == 1:
        return db.update_document(doc["id"], parent_id=subjects[0]["id"]) or doc
    return doc


# =========================================================================== utilitaires

def _fresh(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """Document avec le texte en cours d'édition à plusieurs (en avance de quelques dixièmes de seconde sur la base)."""
    if doc:
        text = live.content(doc["id"])
        if text is not None:
            doc["content"] = text
    return doc


def _get_doc_or_404(doc_id: int) -> dict[str, Any]:
    doc = _fresh(db.get_document(doc_id))
    if not doc:
        raise HTTPException(404, "Document introuvable.")
    return doc


def _set_content(doc_id: int, content: str, **fields: Any) -> dict[str, Any] | None:
    """Enregistre un nouveau texte et l'envoie aux personnes qui ont le document ouvert."""
    doc = db.update_document(doc_id, content=content, **fields)
    live.replace_content(doc_id, content)
    return doc


def _listing(doc: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in doc.items() if k not in {"content", "data"}} | {"size": len(doc.get("content") or "")}
    data = doc.get("data")
    if isinstance(data, dict) and isinstance(data.get("sources"), list):
        out["sources_count"] = len(data["sources"])
    return out


def _ndjson(events: AsyncIterator[dict[str, Any]]) -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        try:
            async for ev in events:
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except llm.OllamaError as exc:
            yield json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False) + "\n"
        except ValueError as exc:
            yield json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False) + "\n"
        except Exception as exc:  # pragma: no cover - filet de sécurité
            log.exception("Erreur pendant le streaming")
            yield json.dumps({"type": "error", "message": f"Erreur inattendue : {exc}"}, ensure_ascii=False) + "\n"

    return StreamingResponse(body(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache"})


def _content_disposition(kind: str, filename: str) -> str:
    ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", filename) or "document"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


# =========================================================================== interface

@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(config.STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.websocket("/api/live")
async def live_channel(websocket: WebSocket) -> None:
    """Temps réel : présence, modifications des autres, édition à plusieurs (voir live.py)."""
    await live.serve(websocket)


@app.get("/api/version")
def version_info() -> dict[str, Any]:
    """Utilisé par run.py et l'interface pour détecter un serveur qui tourne sur un code périmé."""
    return {
        "app": "DeepLearning",
        "version": __version__,
        "code": LOADED_CODE,
        "stale": config.code_fingerprint() != LOADED_CODE,
        "pid": os.getpid(),
        # Processus lancé par run.py (superviseur du rechargement automatique) : c'est lui qu'on arrête.
        "supervisor": int(os.environ["DL_SUPERVISOR_PID"]) if os.environ.get("DL_SUPERVISOR_PID", "").isdigit() else None,
        # Dans Docker, les PID ci-dessus sont ceux du conteneur : run.py ne doit jamais les arrêter.
        "docker": config.IN_DOCKER,
    }


# =========================================================================== comptes

class LoginIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=60)
    password: str = Field(..., min_length=1, max_length=200)


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response) -> dict[str, Any]:
    if not config.AUTH_ENABLED:
        return {"auth": False, "user": auth.public(auth.LOCAL_USER)}
    ip = auth.client_ip(request)
    auth.check_rate(ip, body.username)
    user = auth.login(body.username, body.password)
    if not user:
        auth.record_failure(ip, body.username)
        raise HTTPException(401, "Identifiant ou mot de passe incorrect.")
    auth.clear_failures(body.username)
    auth.start_session(request, response, user["id"])
    db.update_user(user["id"], last_login=db.now())
    log.info("Connexion de %s (%s)", user["username"], ip)
    return {"auth": True, "user": auth.public(user)}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    auth.end_session(request, response)
    return {"ok": True}


@app.get("/api/auth/me")
def me(request: Request) -> dict[str, Any]:
    user = auth.current_user(request)
    if user is None:
        raise HTTPException(401, "Connexion requise.")
    return {"auth": config.AUTH_ENABLED, "user": auth.public(user)}


class PasswordIn(BaseModel):
    current: str = Field(..., min_length=1, max_length=200)
    new: str = Field(..., max_length=200)


@app.post("/api/auth/password")
def change_password(body: PasswordIn, request: Request) -> dict[str, Any]:
    if not config.AUTH_ENABLED:
        raise HTTPException(400, "Les comptes ne sont pas activés sur cette installation.")
    user = request.state.user
    full = db.get_user_for_login(user["username"])
    if not full or not auth.verify_password(body.current, full["password_hash"]):
        raise HTTPException(400, "Mot de passe actuel incorrect.")
    auth.check_new_password(body.new, user["username"])
    if body.new == body.current:
        raise HTTPException(400, "Choisis un mot de passe différent de l'actuel.")
    db.update_user(user["id"], password_hash=auth.hash_password(body.new), must_change=False)
    db.delete_user_sessions(user["id"], keep=auth.session_hash(request))  # déconnecte ses autres appareils
    return {"user": auth.public(db.get_user(user["id"]) or user)}


class UserIn(BaseModel):
    username: str = Field(..., pattern=r"^[\w.-]{2,32}$")
    is_admin: bool = False


class UserPatch(BaseModel):
    is_admin: bool | None = None
    active: bool | None = None


@app.get("/api/users")
def users(_: dict = Depends(require_admin)) -> list[dict[str, Any]]:
    return [auth.public(u) for u in db.list_users()]


@app.post("/api/users")
def create_user(body: UserIn, _: dict = Depends(require_admin)) -> dict[str, Any]:
    if db.get_user_for_login(body.username):
        raise HTTPException(409, "Ce nom d'utilisateur existe déjà.")
    password = auth.temporary_password()
    user = db.create_user(body.username, auth.hash_password(password), is_admin=body.is_admin, must_change=True)
    # Mot de passe provisoire : affiché une seule fois à l'administrateur, à transmettre à la personne.
    return {"user": auth.public(user), "password": password}


@app.patch("/api/users/{user_id}")
def update_user(user_id: int, body: UserPatch, admin: dict = Depends(require_admin)) -> dict[str, Any]:
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(404, "Compte introuvable.")
    fields = body.model_dump(exclude_none=True)
    if user_id == admin["id"] and (fields.get("is_admin") is False or fields.get("active") is False):
        raise HTTPException(400, "Tu ne peux pas retirer tes propres droits ni désactiver ton propre compte.")
    user = db.update_user(user_id, **fields)
    if fields.get("active") is False:
        db.delete_user_sessions(user_id)
        live.disconnect_user(user_id)
    return auth.public(user or target)


@app.post("/api/users/{user_id}/reset")
def reset_user_password(user_id: int, admin: dict = Depends(require_admin)) -> dict[str, Any]:
    if not db.get_user(user_id):
        raise HTTPException(404, "Compte introuvable.")
    if user_id == admin["id"]:
        raise HTTPException(400, "Pour ton propre compte, utilise « Changer mon mot de passe ».")
    password = auth.temporary_password()
    user = db.update_user(user_id, password_hash=auth.hash_password(password), must_change=True)
    db.delete_user_sessions(user_id)
    live.disconnect_user(user_id)
    return {"user": auth.public(user or {}), "password": password}


@app.delete("/api/users/{user_id}")
def delete_user(user_id: int, admin: dict = Depends(require_admin)) -> dict[str, bool]:
    if user_id == admin["id"]:
        raise HTTPException(400, "Tu ne peux pas supprimer ton propre compte.")
    db.delete_user(user_id)
    live.disconnect_user(user_id)
    return {"ok": True}


@app.get("/api/admin/public-url")
async def public_url(_: dict = Depends(require_admin)) -> dict[str, Any]:
    """Adresse publique du site via le tunnel Cloudflare (conteneur « tunnel »)."""
    if not config.TUNNEL_METRICS_URL:
        return {"status": "off", "url": config.PUBLIC_URL or None, "temporary": False}
    base = config.TUNNEL_METRICS_URL
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            ready = await client.get(base + "/ready")
            status = "up" if ready.status_code == 200 else "down"
            quick = (await client.get(base + "/quicktunnel")).json()
            if quick.get("hostname"):
                return {"status": status, "url": f"https://{quick['hostname']}", "temporary": True}
            url = config.PUBLIC_URL or None
            if not url:
                conf = (await client.get(base + "/config")).json()
                ingress = (conf.get("config") or {}).get("ingress") or []
                host = next((r["hostname"] for r in ingress if r.get("hostname") and "*" not in r["hostname"]), None)
                url = f"https://{host}" if host else None
            warning = None
            if status == "up" and not url:
                # Tunnel nommé connecté mais sans route : Cloudflare ne sait pas quelle adresse publier.
                warning = (
                    "Tunnel connecté, mais aucune adresse ne lui est associée : dans Cloudflare (Zero Trust → "
                    "Networks → Tunnels → ce tunnel), ajoute une route publique (« Public Hostname » / « Published "
                    "application route ») vers le service HTTP app:8765."
                )
            return {"status": status, "url": url, "temporary": False, "warning": warning}
    except (httpx.HTTPError, ValueError):
        return {"status": "down", "url": config.PUBLIC_URL or None, "temporary": False}


# =========================================================================== Ollama & réglages

@app.get("/api/health")
async def health() -> dict[str, Any]:
    version = await llm.version()
    result: dict[str, Any] = {"ollama": version is not None, "version": version, "models": [], "model": None}
    if version:
        try:
            result["models"] = await llm.list_models()
            model = await llm.resolve_model(db.get_settings())
            result["model"] = model
            result["capabilities"] = await llm.capabilities(model)
        except llm.OllamaError as exc:
            result["error"] = str(exc)
    return result


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return db.get_settings()


class SettingsIn(BaseModel):
    model: str | None = None
    num_ctx: int | None = Field(None, ge=2048, le=262144)
    temperature: float | None = Field(None, ge=0, le=2)
    think: bool | None = None
    current_semester: str | None = Field(None, pattern=r"^(S\d{1,2})?$")


@app.put("/api/settings")
def put_settings(body: SettingsIn, _: dict = Depends(require_admin)) -> dict[str, Any]:
    # Réglages communs à tout le monde (modèle, mémoire…) : réservés aux administrateurs.
    return db.update_settings(body.model_dump(exclude_none=True))


# =========================================================================== matières

class SubjectIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    semester: str | None = Field(None, pattern=r"^(S\d{1,2})?$")
    position: int | None = None


@app.get("/api/subjects")
def subjects() -> list[dict[str, Any]]:
    return db.list_subjects()


@app.post("/api/subjects")
def create_subject(body: SubjectIn) -> dict[str, Any]:
    if not body.name or not body.name.strip():
        raise HTTPException(400, "Le nom de la matière est obligatoire.")
    try:
        subject = db.create_subject(body.name.strip(), body.color, body.semester or "", body.position)
    except Exception as exc:
        raise HTTPException(400, "Une matière porte déjà ce nom dans ce semestre.") from exc
    live.notify("subjects", msg=f"a créé la matière « {subject['name']} »")
    return subject


@app.patch("/api/subjects/{subject_id}")
def update_subject(subject_id: int, body: SubjectIn) -> dict[str, Any]:
    fields = body.model_dump(exclude_none=True)
    if "name" in fields:
        fields["name"] = fields["name"].strip()
    try:
        subject = db.update_subject(subject_id, **fields)
    except Exception as exc:
        raise HTTPException(400, "Une matière porte déjà ce nom dans ce semestre.") from exc
    if not subject:
        raise HTTPException(404, "Matière introuvable.")
    live.notify("subjects")
    return subject


@app.delete("/api/subjects/{subject_id}")
def delete_subject(subject_id: int) -> dict[str, bool]:
    subject = next((s for s in db.list_subjects() if s["id"] == subject_id), None)
    db.delete_subject(subject_id)
    if subject:
        live.notify("subjects", msg=f"a supprimé la matière « {subject['name']} »")
    return {"ok": True}


# =========================================================================== documents

@app.get("/api/documents")
def documents(subject_id: int | None = None) -> list[dict[str, Any]]:
    return db.list_documents(subject_id)


@app.get("/api/search")
def search(q: str) -> list[dict[str, Any]]:
    q = q.strip()
    return db.search_documents(q) if len(q) >= 2 else []


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: int) -> dict[str, Any]:
    doc = _get_doc_or_404(doc_id)
    doc["scanned_pages"] = [int(m.group(1)) for m in importers.SCAN_MARKER_RE.finditer(doc["content"] or "")]
    doc["lang"] = llm.detect_language(doc["content"] or "")
    return doc


class DocumentIn(BaseModel):
    title: str | None = Field(None, max_length=300)
    content: str | None = None
    kind: str | None = None
    subject_id: int | None = None
    parent_id: int | None = None


@app.post("/api/documents")
def create_document(body: DocumentIn) -> dict[str, Any]:
    kind = body.kind if body.kind in KINDS else "note"
    doc = db.create_document(
        (body.title or "Nouvelle note").strip() or "Nouvelle note",
        body.content or "",
        kind,
        body.subject_id,
        body.parent_id,
    )
    live.notify("docs", doc=doc["id"], msg=f"a créé « {doc['title']} »")
    return doc


@app.patch("/api/documents/{doc_id}")
def update_document(doc_id: int, body: DocumentIn) -> dict[str, Any]:
    _get_doc_or_404(doc_id)
    fields = body.model_dump(exclude_unset=True)
    if "kind" in fields and fields["kind"] not in KINDS:
        raise HTTPException(400, "Type de document inconnu.")
    if "title" in fields:
        fields["title"] = (fields["title"] or "").strip() or "Sans titre"
    content = fields.pop("content", None)
    doc = db.update_document(doc_id, **fields) if fields else None
    if content is not None:  # éditeur hors ligne (sans le canal temps réel)
        doc = _set_content(doc_id, content)
    if fields:
        live.notify("docs", doc=doc_id, fields=sorted(fields))
    doc = doc or db.get_document(doc_id)
    return _listing(doc) if doc else {}


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: int) -> dict[str, bool]:
    doc = db.get_document(doc_id)
    source = db.delete_document(doc_id)
    if source:
        path = config.UPLOAD_DIR / source
        path.unlink(missing_ok=True)
        office.preview_path(path).unlink(missing_ok=True)
    if doc:
        live.forget(doc_id)
        live.notify("docs", doc=doc_id, msg=f"a supprimé « {doc['title']} »")
    return {"ok": True}


@app.get("/api/documents/{doc_id}/preview")
@app.get("/api/documents/{doc_id}/preview/{filename}")  # nom de fichier dans l'URL = titre du lecteur PDF
async def document_preview(doc_id: int, filename: str | None = None) -> FileResponse:
    """Le cours d'origine en PDF, pour l'afficher dans l'outil (Word / PowerPoint convertis via Office)."""
    doc = _get_doc_or_404(doc_id)
    if not doc.get("source_file"):
        raise HTTPException(404, "Ce document n'a pas de fichier d'origine.")
    path = config.UPLOAD_DIR / doc["source_file"]
    if not path.exists():
        raise HTTPException(404, "Fichier d'origine introuvable.")
    if path.suffix.lower() != ".pdf":
        try:
            path = await asyncio.to_thread(office.to_pdf, path)
        except office.ConversionError as exc:
            raise HTTPException(501, str(exc)) from exc
    name = Path(doc.get("source_name") or path.name).stem + ".pdf"
    return FileResponse(path, media_type="application/pdf", filename=name, content_disposition_type="inline")


@app.get("/api/documents/{doc_id}/source")
def document_source(doc_id: int) -> FileResponse:
    doc = _get_doc_or_404(doc_id)
    if not doc.get("source_file"):
        raise HTTPException(404, "Ce document n'a pas de fichier d'origine.")
    path = config.UPLOAD_DIR / doc["source_file"]
    if not path.exists():
        raise HTTPException(404, "Fichier d'origine introuvable.")
    return FileResponse(path, filename=doc.get("source_name") or path.name, content_disposition_type="inline")


@app.get("/api/documents/{doc_id}/export")
def export_document(doc_id: int, format: str = "md") -> Response:
    doc = _get_doc_or_404(doc_id)
    safe = re.sub(r'[\\/:*?"<>|]+', "_", doc["title"]).strip() or "document"
    if format == "html":
        html = render.export_html(doc["title"], doc["content"])
        return Response(html, media_type="text/html; charset=utf-8")
    return Response(
        doc["content"],
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _content_disposition("attachment", f"{safe}.md")},
    )


class RenderIn(BaseModel):
    markdown: str = ""


@app.post("/api/render")
def render_markdown(body: RenderIn) -> dict[str, str]:
    return {"html": render.to_html(body.markdown)}


# =========================================================================== import

@app.post("/api/import")
async def import_files(
    files: list[UploadFile] = File(...),
    subject_id: str | None = Form(None),
    kinds: list[str] = Form(default=[]),  # type de chaque fichier (cours/td/tp), dans l'ordre ; sinon détecté
) -> list[dict[str, Any]]:
    sid = int(subject_id) if subject_id and subject_id.strip().isdigit() else None
    results: list[dict[str, Any]] = []
    max_bytes = config.MAX_UPLOAD_MB * 1024 * 1024
    for index, upload in enumerate(files):
        name = Path(upload.filename or "fichier").name
        ext = Path(name).suffix.lower()
        if ext not in config.ALLOWED_IMPORT_EXT:
            results.append({"filename": name, "error": f"Format non pris en charge ({ext or 'inconnu'})."})
            continue
        stored = f"{uuid.uuid4().hex}{ext}"
        path = config.UPLOAD_DIR / stored
        size = 0
        too_big = False
        with path.open("wb") as fh:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    too_big = True
                    break
                fh.write(chunk)
        if too_big:
            path.unlink(missing_ok=True)
            results.append({"filename": name, "error": f"Fichier trop volumineux (> {config.MAX_UPLOAD_MB} Mo)."})
            continue
        try:
            res = await asyncio.to_thread(importers.import_file, path, name)
        except Exception as exc:
            log.exception("Import impossible : %s", name)
            path.unlink(missing_ok=True)
            results.append({"filename": name, "error": f"Lecture impossible : {exc}"})
            continue
        keep_source = ext in {".pdf", ".docx", ".pptx", ".doc", ".ppt"}
        if not keep_source:
            path.unlink(missing_ok=True)
        kind = kinds[index] if index < len(kinds) and kinds[index] in SOURCE_KINDS else importers.guess_kind(name)
        doc = db.create_document(
            res.title,
            res.markdown,
            kind,
            sid,
            source_file=stored if keep_source else None,
            source_name=name,
        )
        results.append(
            {
                "filename": name,
                "document": _listing(doc),
                "warnings": res.warnings,
                "scanned_pages": len(res.scanned_pages),
            }
        )
    # Comptes-rendus : rattachement à leur sujet (après coup, le sujet peut être dans le même envoi).
    for r in results:
        if r.get("document", {}).get("kind") == "rendu":
            linked = _link_report(db.get_document(r["document"]["id"]) or {})
            if linked:
                r["document"] = _listing(linked)
    # Langue détectée (pour proposer la traduction des cours en anglais).
    for r in results:
        if "document" in r:
            full = db.get_document(r["document"]["id"])
            r["document"]["lang"] = llm.detect_language(full["content"]) if full else None
    added = [r["document"] for r in results if "document" in r]
    if added:
        what = f"« {added[0]['title']} »" if len(added) == 1 else f"{len(added)} cours"
        live.notify("docs", doc=added[0]["id"] if len(added) == 1 else None, msg=f"a importé {what}")
    return results


# =========================================================================== génération IA

class GenerateIn(BaseModel):
    document_id: int
    task: str
    count: int = Field(10, ge=1, le=60)
    difficulty: str = "moyen"
    instructions: str = Field("", max_length=4000)
    selection: str | None = None
    # Plusieurs CM de la même matière → un seul document (ex. une fiche de révision pour toute la matière).
    document_ids: list[int] | None = Field(None, max_length=80)


def _normalize_qcm(data: Any) -> list[dict[str, Any]]:
    items = data.get("questions") if isinstance(data, dict) else data
    out = []
    for q in items or []:
        if not isinstance(q, dict):
            continue
        question = str(q.get("question", "")).strip()
        choices = [re.sub(r"^\s*[A-Da-d1-4][\).:-]\s+", "", str(c)).strip() for c in q.get("choices") or []]
        choices = [c for c in choices if c]
        answer = q.get("answer")
        idx = -1
        if isinstance(answer, int):
            idx = answer
        elif isinstance(answer, str):
            a = answer.strip()
            if a.isdigit():
                idx = int(a)
            elif len(a) == 1 and a.upper() in "ABCD":
                idx = "ABCD".index(a.upper())
            elif a in choices:
                idx = choices.index(a)
        if question and len(choices) >= 2 and 0 <= idx < len(choices):
            out.append(
                {
                    "question": question,
                    "choices": choices,
                    "answer": idx,
                    "explanation": str(q.get("explanation", "")).strip(),
                }
            )
    return out


def _qcm_markdown(title: str, questions: list[dict[str, Any]]) -> str:
    letters = "ABCDEFGH"
    lines = [f"# QCM — {title}", ""]
    for n, q in enumerate(questions, start=1):
        lines.append(f"**{n}. {q['question']}**")
        lines.append("")
        lines += [f"- {letters[i]}. {c}" for i, c in enumerate(q["choices"])]
        lines.append("")
    lines += ["---", "", "## Corrigé", ""]
    for n, q in enumerate(questions, start=1):
        good = q["choices"][q["answer"]]
        expl = f" — {q['explanation']}" if q["explanation"] else ""
        lines.append(f"{n}. **{letters[q['answer']]}. {good}**{expl}")
    return "\n".join(lines) + "\n"


def _normalize_cards(data: Any) -> list[dict[str, str]]:
    items = data.get("cards") if isinstance(data, dict) else data
    out = []
    for c in items or []:
        if isinstance(c, dict):
            front, back = str(c.get("front", "")).strip(), str(c.get("back", "")).strip()
            if front and back:
                out.append({"front": front, "back": back})
    return out


def _cards_markdown(title: str, cards: list[dict[str, Any]]) -> str:
    def cell(s: str) -> str:
        return s.replace("|", "\\|").replace("\n", "<br>")

    lines = [f"# {title}", "", "| Recto | Verso |", "| --- | --- |"]
    lines += [f"| {cell(c['front'])} | {cell(c['back'])} |" for c in cards]
    return "\n".join(lines) + "\n"


def _sync_cards_content(doc_id: int) -> None:
    doc = db.get_document(doc_id)
    if doc and doc["kind"] == "flashcards":
        _set_content(doc_id, _cards_markdown(doc["title"], db.list_cards(doc_id)))
    live.notify("cards", doc=doc_id)


@app.get("/api/tasks")
def tasks() -> dict[str, Any]:
    return {
        "tasks": {k: {"label": v["label"], "kind": v["kind"]} for k, v in prompts.TASKS.items()},
        "transforms": list(prompts.TRANSFORMS),
    }


@app.post("/api/generate")
async def generate(body: GenerateIn) -> StreamingResponse:
    doc = _get_doc_or_404(body.document_id)
    spec = prompts.TASKS.get(body.task)
    if not spec:
        raise HTTPException(400, "Tâche inconnue.")
    if body.task == "custom" and not body.instructions.strip():
        raise HTTPException(400, "Écris ta demande dans le champ « Consignes ».")
    if body.document_ids and len(set(body.document_ids)) > 1:
        return _generate_multi(doc, spec, body)
    use_selection = bool(body.selection and body.selection.strip())
    source_text = body.selection.strip() if use_selection else (doc["content"] or "")
    if not source_text.strip():
        raise HTTPException(400, "Le document est vide : rien à analyser.")
    settings = db.get_settings()
    # Relecture d'un compte-rendu : on joint le sujet de TD/TP auquel il est rattaché.
    subject: tuple[str, str] | None = None
    reserve = spec["reserve"]
    if body.task == "relecture" and doc.get("parent_id"):
        parent = _fresh(db.get_document(doc["parent_id"]))
        if parent and parent["content"].strip():
            limit = llm.input_budget_chars(settings, reserve) // 2
            text = parent["content"]
            if len(text) > limit:
                text = text[:limit] + "\n\n[…sujet tronqué…]"
            subject = (parent["title"], text)
            reserve += llm.estimate_tokens(text)

    async def run() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "status", "message": "Préparation du cours…"}
        if subject:
            yield {"type": "status", "message": f"Sujet joint à la relecture : « {subject[0]} »."}
        source = source_text
        async for ev in llm.fit_source(source_text, settings, reserve):
            if ev["type"] == "source":
                source = ev["text"]
                if ev["condensed"]:
                    yield {"type": "status", "message": "Cours condensé pour tenir dans la mémoire du modèle. Génération…"}
            else:
                yield ev
        yield {"type": "status", "message": "Génération en cours…"}

        messages = [
            {"role": "system", "content": prompts.SYSTEM_TUTOR},
            {
                "role": "user",
                "content": prompts.task_user_prompt(
                    body.task, doc["title"], source, body.count, body.difficulty, body.instructions.strip(),
                    doc_kind=doc["kind"], subject=subject,
                ),
            },
        ]
        async for ev in _write_result(
            body, spec, messages, settings, spec["reserve"],
            title=f"{spec['label']} — {doc['title']}" + (" (extrait)" if use_selection else ""),
            short_title=doc["title"],
            subject_id=doc["subject_id"],
            parent_id=doc["id"],
        ):
            yield ev

    return _ndjson(run())


async def _write_result(
    body: GenerateIn,
    spec: dict[str, Any],
    messages: list[dict[str, Any]],
    settings: dict[str, Any],
    reserve: int,
    *,
    title: str,
    short_title: str,
    subject_id: int | None,
    parent_id: int | None,
    sources: list[int] | None = None,
    header: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Appelle le modèle (en streaming), puis enregistre le document produit."""
    schema = spec.get("schema")
    think = False if schema else None
    thinking_on = bool(settings.get("think")) and think is None
    parts: list[str] = []
    info: dict[str, Any] = {}
    async for ev in llm.chat_stream(
        messages,
        settings,
        fmt=schema,
        num_predict=None if thinking_on else reserve + 800,
        think=think,
        temperature=0.3 if schema else None,
    ):
        if ev["type"] == "token":
            parts.append(ev["text"])
            yield ev
        elif ev["type"] == "thinking":
            yield ev
        elif ev["type"] == "done":
            info = ev

    text = "".join(parts).strip()
    if not text:
        raise ValueError("Le modèle n'a renvoyé aucun texte.")
    data: dict[str, Any] | None = {"sources": sources} if sources else None
    cards: list[dict[str, str]] = []
    if body.task == "qcm":
        questions = _normalize_qcm(llm.parse_json_loose(text))
        if not questions:
            raise ValueError("Le modèle n'a pas produit de questions exploitables. Réessaie.")
        data = (data or {}) | {"questions": questions}
        content = _qcm_markdown(short_title, questions)
    elif body.task == "flashcards":
        cards = _normalize_cards(llm.parse_json_loose(text))
        if not cards:
            raise ValueError("Le modèle n'a pas produit de cartes exploitables. Réessaie.")
        content = _cards_markdown(title, cards)
    else:
        content = re.sub(r"^```(?:markdown|md)?\s*\n(.*?)\n```\s*$", r"\1", text, flags=re.S)
    if header:
        # Juste sous le titre principal : la liste des CM couverts.
        m = re.match(r"\s*#\s[^\n]*\n", content)
        content = content[: m.end()] + f"\n{header}\n" + content[m.end():] if m else f"{header}\n\n{content}"

    new_doc = db.create_document(title, content, spec["kind"], subject_id=subject_id, parent_id=parent_id, data=data)
    if cards:
        db.add_cards(new_doc["id"], cards)
        live.notify("cards", doc=new_doc["id"])
    live.notify("docs", doc=new_doc["id"], msg=f"a généré « {title} »")
    warning = None
    if info.get("done_reason") == "length":
        warning = "La réponse a été coupée (limite de longueur atteinte). Augmente le contexte dans les réglages."
    yield {"type": "done", "document": _listing(new_doc), "info": info, "warning": warning}


def _share_budget(sizes: list[int], budget: int) -> list[int]:
    """Partage équitable du contexte entre plusieurs cours : les petits gardent tout leur texte, le reste
    est réparti à parts égales entre les plus longs."""
    shares = [0] * len(sizes)
    remaining = budget
    order = sorted(range(len(sizes)), key=lambda i: sizes[i])
    for k, i in enumerate(order):
        shares[i] = min(sizes[i], remaining // (len(sizes) - k))
        remaining -= shares[i]
    return shares


def _generate_multi(doc: dict[str, Any], spec: dict[str, Any], body: GenerateIn) -> StreamingResponse:
    """Un seul document (fiche, résumé, QCM…) à partir de plusieurs CM de la même matière."""
    if body.task in prompts.MULTI_EXCLUDED:
        raise HTTPException(400, "Cette tâche porte sur un seul document : choisis « Ce document ».")
    subject_id = doc.get("subject_id")
    subject = next((s for s in db.list_subjects() if s["id"] == subject_id), None)
    if not subject:
        raise HTTPException(400, "Ce document n'est rangé dans aucune matière.")
    ids = list(dict.fromkeys(body.document_ids or []))
    docs = [d for d in (_fresh(db.get_document(i)) for i in ids) if d]
    if any(d["subject_id"] != subject_id for d in docs):
        raise HTTPException(400, "Les documents choisis doivent appartenir à la même matière.")
    docs = [d for d in docs if (d["content"] or "").strip()]
    if len(docs) < 2:
        raise HTTPException(400, "Choisis au moins deux CM qui ont du contenu.")

    siblings = db.list_documents(subject_id)
    all_cm = {d["id"] for d in siblings if d["kind"] == "cours"}
    every = all_cm <= {d["id"] for d in docs}
    # Fiche de révision déjà générée pour un CM (la plus récente) : réutilisée si le CM est trop long.
    fiches: dict[int, dict[str, Any]] = {}
    for d in sorted(siblings, key=lambda x: x["updated_at"]):
        if d["kind"] == "fiche" and d["parent_id"]:
            fiches[d["parent_id"]] = d

    settings = db.get_settings()
    reserve = spec["reserve"] + prompts.MULTI_EXTRA_RESERVE.get(body.task, 0)
    n = len(docs)
    scope = "tous les CM" if every else f"{n} CM"

    async def run() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "status", "message": f"Préparation des {n} CM…"}
        budget = llm.input_budget_chars(settings, reserve) - 100 * n  # balises <cours …> autour de chaque CM
        shares = _share_budget([len(d["content"]) for d in docs], budget)
        sources: list[tuple[str, str]] = []
        shortened = 0
        for d, share in zip(docs, shares):
            text = d["content"]
            if len(text) > share:
                shortened += 1
                fiche = fiches.get(d["id"])
                fiche_text = (_fresh(db.get_document(fiche["id"])) or {}).get("content", "") if fiche else ""
                if fiche_text.strip() and len(fiche_text) <= share:
                    yield {"type": "status", "message": f"« {d['title']} » est long : sa fiche de révision est utilisée."}
                    text = fiche_text
                else:
                    async for ev in llm.fit_source(text, settings, reserve, budget=share, label=d["title"]):
                        if ev["type"] == "source":
                            text = ev["text"]
                        else:
                            yield ev
            sources.append((d["title"], text))
        what = spec["label"].lower()
        yield {"type": "status", "message": f"Génération ({what} sur {n} CM)…"}

        messages = [
            {"role": "system", "content": prompts.SYSTEM_TUTOR},
            {
                "role": "user",
                "content": prompts.multi_user_prompt(
                    body.task, subject["name"], sources, body.count, body.difficulty, body.instructions.strip()
                ),
            },
        ]
        header = f"> 📚 Couvre {n} CM : " + " · ".join(f"**CM {i}** {d['title']}" for i, d in enumerate(docs, start=1))
        if shortened:
            header += f"  \n> *({shortened} CM trop long(s) pour le modèle ont été résumés au préalable.)*"
        async for ev in _write_result(
            body, spec, messages, settings, reserve,
            title=f"{spec['label']} — {subject['name']} ({scope})",
            short_title=f"{subject['name']} ({scope})",
            subject_id=subject_id,
            parent_id=None,
            sources=[d["id"] for d in docs],
            header=header,
        ):
            yield ev

    return _ndjson(run())


class TransformIn(BaseModel):
    text: str = Field(..., min_length=1)
    action: str
    instructions: str = Field("", max_length=4000)


@app.post("/api/transform")
async def transform(body: TransformIn) -> StreamingResponse:
    settings = db.get_settings()
    if body.action == "custom":
        if not body.instructions.strip():
            raise HTTPException(400, "Écris ta consigne.")
        instruction = body.instructions.strip()
    elif body.action in prompts.TRANSFORMS:
        instruction = prompts.TRANSFORMS[body.action]
        if body.instructions.strip():
            instruction += f"\nConsignes supplémentaires : {body.instructions.strip()}"
    else:
        raise HTTPException(400, "Action inconnue.")
    expand = body.action in {"developper", "exemples", "custom"}
    reserve = min(8000, llm.estimate_tokens(body.text) * (3 if expand else 2) + 600)
    if len(body.text) > llm.input_budget_chars(settings, reserve):
        raise HTTPException(400, "Le texte sélectionné est trop long pour le modèle : sélectionne une partie plus courte.")

    messages = [
        {"role": "system", "content": prompts.SYSTEM_WRITER},
        {"role": "user", "content": f"{instruction}\nRenvoie uniquement le résultat.\n\n<texte>\n{body.text}\n</texte>"},
    ]

    async def run() -> AsyncIterator[dict[str, Any]]:
        async for ev in llm.chat_stream(messages, settings, num_predict=reserve, think=False):
            yield ev

    return _ndjson(run())


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatIn(BaseModel):
    document_id: int | None = None
    messages: list[ChatMessage]
    attachments: list[str] = Field(default_factory=list, max_length=12)  # fichiers donnés à l'IA (/api/attachments)


def _passages(texts: list[tuple[str, str]], query: str, budget: int) -> tuple[list[tuple[str, str]], bool]:
    """Partage le contexte entre plusieurs textes ; garde les passages les plus utiles de chacun."""
    out: list[tuple[str, str]] = []
    partial = False
    for (name, text), share in zip(texts, _share_budget([len(t) for _, t in texts], max(3000, budget))):
        context, cut = llm.select_passages(text, query, max(1500, share))
        out.append((name, context))
        partial = partial or cut
    return out, partial


def _subject_named(name: Any, fallback: int | None) -> int | None:
    """Matière désignée par l'IA (« Réseaux »…), sinon celle du cours ouvert."""
    wanted = str(name or "").strip().casefold()
    if wanted:
        subjects = db.list_subjects()
        current = db.get_settings().get("current_semester") or ""
        exact = [s for s in subjects if s["name"].casefold() == wanted]
        close = [s for s in subjects if wanted in s["name"].casefold() or s["name"].casefold() in wanted]
        for group in (exact, close):
            if group:
                return sorted(group, key=lambda s: (s.get("semester") or "") != current)[0]["id"]
    return fallback


@app.post("/api/chat")
async def chat(body: ChatIn) -> StreamingResponse:
    settings = db.get_settings()
    history = [m for m in body.messages if m.role in {"user", "assistant"} and m.content.strip()]
    if not history or history[-1].role != "user":
        raise HTTPException(400, "Aucune question.")
    # On garde les derniers échanges dans une limite raisonnable.
    kept: list[ChatMessage] = []
    total = 0
    for m in reversed(history):
        total += len(m.content)
        if kept and (total > 14000 or len(kept) >= 14):
            break
        kept.append(m)
    kept.reverse()

    doc = _get_doc_or_404(body.document_id) if body.document_id is not None else None
    files = [a for a in (attachments.load(i) for i in dict.fromkeys(body.attachments)) if a]
    texts = ([(doc["title"], doc["content"] or "")] if doc else []) + [(f["name"], f["text"]) for f in files if f["text"].strip()]
    images = [img for f in files for img in f.get("images") or []][:8]
    query = " ".join(m.content for m in kept if m.role == "user")[-2000:]
    budget = llm.input_budget_chars(settings, 2500) - sum(len(m.content) for m in kept)
    contexts, partial = _passages(texts, query, budget)
    current = settings.get("current_semester") or ""
    subjects = [s["name"] for s in sorted(db.list_subjects(), key=lambda s: (s.get("semester") or "") != current)]
    system = prompts.chat_system_prompt(
        contexts[0] if doc else None, contexts[1:] if doc else contexts, partial, subjects, len(images)
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    messages += [{"role": m.role, "content": m.content} for m in kept]
    if images:
        messages[-1]["images"] = images
    request = kept[-1].content

    async def run() -> AsyncIterator[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        async for ev in llm.chat_stream(messages, settings, tools=[prompts.CREATE_TOOL]):
            if ev["type"] == "tool_call":
                if ev["name"] == "creer_document":
                    calls.append(ev["arguments"])
                continue
            yield ev
        for n, args in enumerate(calls[:3], start=1):
            async for ev in _create_from_chat(n, args, doc, files, request, settings):
                yield ev

    return _ndjson(run())


def _create_kind(raw: Any, title: str) -> str:
    """Type de document demandé par l'IA (« examen_blanc » → examen), à défaut d'après le titre."""
    def plain(s: Any) -> str:
        s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().casefold()
        return re.sub(r"[_\-/]+", " ", s).strip()

    for text in (plain(raw), plain(title)):  # « note » n'est retenu que si le titre n'en dit pas plus
        if text in prompts.CREATE_TYPES and text != "note":
            return text
        for pattern, kind in prompts.CREATE_KIND_RULES:
            if re.search(pattern, text):
                return kind
    return "note"


async def _create_from_chat(
    n: int,
    args: dict[str, Any],
    doc: dict[str, Any] | None,
    files: list[dict[str, Any]],
    request: str,
    settings: dict[str, Any],
) -> AsyncIterator[dict[str, Any]]:
    """L'IA a décidé de créer un document : rédaction (en direct dans la discussion) puis ajout à la bibliothèque."""
    log.info("Document demandé à l'IA : %s", {k: str(v)[:120] for k, v in args.items()})
    title = re.sub(r"\s+", " ", str(args.get("titre") or "")).strip()[:200]
    kind = _create_kind(args.get("type"), title)
    spec = prompts.CREATE_TYPES[kind]
    title = title or f"{spec['label']} — {request.strip()[:60]}"
    instructions = str(args.get("consignes") or request).strip()[:4000]
    subject_id = _subject_named(args.get("matiere"), doc["subject_id"] if doc else None)
    yield {"type": "create", "n": n, "kind": spec["kind"], "title": title}
    try:
        names = ([doc["title"]] if doc else []) + [f["name"] for f in files]
        texts = ([(doc["title"], doc["content"] or "")] if doc else []) + [(f["name"], f["text"]) for f in files if f["text"].strip()]
        images = [img for f in files for img in f.get("images") or []][:8]
        # Les documents dérivés (fiche, QCM…) se rangent sous le cours ouvert ; un cours, TD, TP ou examen à part.
        parent_id = doc["id"] if doc and spec["kind"] not in SOURCE_KINDS else None

        if spec.get("task"):  # QCM et flashcards : sortie structurée, comme dans « Générer »
            task = spec["task"]
            tspec = prompts.TASKS[task]
            try:
                count = max(1, min(60, int(args.get("nombre") or spec["count"])))
            except (TypeError, ValueError):
                count = spec["count"]
            sources, _ = _passages(texts, f"{instructions} {request}", llm.input_budget_chars(settings, tspec["reserve"]))
            source = "\n\n".join(f"## {t}\n\n{x}" for t, x in sources) or (
                f"(Pas de document fourni : appuie-toi sur tes connaissances.)\nSujet demandé : {instructions}"
            )
            gen = GenerateIn(document_id=doc["id"] if doc else 0, task=task, count=count, instructions=instructions)
            messages = [
                {"role": "system", "content": prompts.SYSTEM_TUTOR},
                {"role": "user", "content": prompts.task_user_prompt(task, title, source, count, "moyen", instructions)},
            ]
            async for ev in _write_result(gen, tspec, messages, settings, tspec["reserve"], title=title,
                                          short_title=title, subject_id=subject_id, parent_id=parent_id):
                if ev["type"] == "token":
                    yield {"type": "create_token", "n": n, "text": ev["text"]}
                elif ev["type"] == "done":
                    yield {"type": "created", "n": n, "document": ev["document"], "warning": ev.get("warning")}
            return

        reserve = spec["reserve"]
        sources, _ = _passages(texts, f"{instructions} {request}", llm.input_budget_chars(settings, reserve)) if texts else ([], False)
        user: dict[str, Any] = {"role": "user", "content": prompts.create_prompt(kind, title, instructions, sources, request, len(images))}
        if images:
            user["images"] = images
        parts: list[str] = []
        info: dict[str, Any] = {}
        async for ev in llm.chat_stream(
            [{"role": "system", "content": prompts.SYSTEM_AUTHOR}, user], settings, num_predict=reserve + 1500, think=False
        ):
            if ev["type"] == "token":
                parts.append(ev["text"])
                yield {"type": "create_token", "n": n, "text": ev["text"]}
            elif ev["type"] == "done":
                info = ev
        content = re.sub(r"^```(?:markdown|md)?\s*\n(.*?)\n```\s*$", r"\1", "".join(parts).strip(), flags=re.S)
        if not content:
            raise ValueError("Le modèle n'a renvoyé aucun texte.")
        if not content.lstrip().startswith("# "):
            content = f"# {title}\n\n{content}"
        data = {"ia": {"demande": instructions, "sources": names}}
        new_doc = db.create_document(title, content + "\n", spec["kind"], subject_id=subject_id, parent_id=parent_id, data=data)
        live.notify("docs", doc=new_doc["id"], msg=f"a fait rédiger « {title} » par l'IA")
        warning = None
        if info.get("done_reason") == "length":
            warning = "Le document a été coupé (limite de longueur atteinte) : demande une version plus courte ou en plusieurs parties."
        yield {"type": "created", "n": n, "document": _listing(new_doc), "warning": warning}
    except (llm.OllamaError, ValueError) as exc:
        yield {"type": "create_error", "n": n, "message": str(exc)}


@app.post("/api/attachments")
async def upload_attachments(files: list[UploadFile] = File(...)) -> list[dict[str, Any]]:
    """Fichiers donnés directement à l'IA : lus et convertis, mais pas ajoutés à la bibliothèque."""
    await asyncio.to_thread(attachments.cleanup)
    attachments.ATT_DIR.mkdir(parents=True, exist_ok=True)
    max_bytes = attachments.MAX_MB * 1024 * 1024
    vision: bool | None = None
    out: list[dict[str, Any]] = []
    for upload in files[:10]:
        name = Path(upload.filename or "fichier").name
        ext = Path(name).suffix.lower()
        if ext not in attachments.ALLOWED_EXT:
            out.append({"name": name, "error": f"Format non pris en charge ({ext or 'inconnu'})."})
            continue
        tmp = attachments.ATT_DIR / f"tmp-{uuid.uuid4().hex}{ext}"
        size = 0
        try:
            with tmp.open("wb") as fh:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError(f"Fichier trop volumineux (> {attachments.MAX_MB} Mo).")
                    fh.write(chunk)
            res = await asyncio.to_thread(attachments.create, tmp, name)
        except ValueError as exc:
            out.append({"name": name, "error": str(exc)})
            continue
        except Exception as exc:
            log.warning("Pièce jointe illisible : %s (%s)", name, exc)
            out.append({"name": name, "error": "Lecture impossible : fichier endommagé ou d'un format non reconnu."})
            continue
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass  # Windows : fichier encore ouvert par le lecteur PDF, effacé au prochain nettoyage
        if res["images"]:
            if vision is None:
                try:
                    vision = "vision" in await llm.capabilities(await llm.resolve_model(db.get_settings()))
                except llm.OllamaError:
                    vision = True
            if not vision:
                res["warning"] = "Le modèle choisi ne lit pas les images : choisis un modèle avec la vision dans les réglages."
        out.append(res)
    return out


@app.post("/api/documents/{doc_id}/ocr")
async def ocr_document(doc_id: int) -> StreamingResponse:
    doc = _get_doc_or_404(doc_id)
    pages = [int(m.group(1)) for m in importers.SCAN_MARKER_RE.finditer(doc["content"] or "")]
    if not pages:
        raise HTTPException(400, "Aucune page scannée à transcrire dans ce document.")
    if not doc.get("source_file") or not doc["source_file"].endswith(".pdf"):
        raise HTTPException(400, "Le PDF d'origine n'est plus disponible.")
    path = config.UPLOAD_DIR / doc["source_file"]
    if not path.exists():
        raise HTTPException(400, "Le PDF d'origine est introuvable.")
    settings = db.get_settings()

    async def run() -> AsyncIterator[dict[str, Any]]:
        model = await llm.resolve_model(settings)
        if "vision" not in await llm.capabilities(model):
            raise ValueError(f"Le modèle « {model} » ne lit pas les images. Choisis un modèle avec la vision.")
        for i, page in enumerate(pages, start=1):
            yield {"type": "status", "message": f"Lecture de la page {page} ({i}/{len(pages)})…"}
            png = await asyncio.to_thread(importers.render_pdf_page_png, path, page)
            image = base64.b64encode(png).decode("ascii")
            parts: list[str] = []
            async for ev in llm.chat_stream(
                [{"role": "user", "content": prompts.OCR_PROMPT, "images": [image]}],
                settings,
                think=False,
                temperature=0.1,
                num_predict=4000,
            ):
                if ev["type"] == "token":
                    parts.append(ev["text"])
                    yield ev
            text = "".join(parts).strip() or "*(page vide)*"
            current = _fresh(db.get_document(doc_id))
            if not current:
                return
            new_content = re.sub(
                rf"^> 📷 \*\*Page {page}\*\*.*$",
                lambda _m: f"*(Page {page} — transcrite par l'IA)*\n\n{text}",
                current["content"],
                count=1,
                flags=re.MULTILINE,
            )
            _set_content(doc_id, new_content)
            yield {"type": "page_done", "page": page}
        yield {"type": "done", "document": _listing(db.get_document(doc_id) or doc)}

    return _ndjson(run())


# =========================================================================== traduction

def _strip_wrappers(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^</?texte>\s*|\s*</?texte>$", "", text)
    m = re.fullmatch(r"```(?:markdown|md)\s*\n(.*?)\n```", text, flags=re.S)
    return (m.group(1) if m else text).strip()


@app.post("/api/documents/{doc_id}/translate")
async def translate_document(doc_id: int) -> StreamingResponse:
    """Traduit un cours anglais en français, section par section ; l'anglais est conservé pour revenir en arrière."""
    doc = _get_doc_or_404(doc_id)
    content = doc["content"] or ""
    if not content.strip():
        raise HTTPException(400, "Le document est vide.")
    data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
    if data.get("translation"):
        raise HTTPException(400, "Ce document a déjà été traduit.")
    settings = db.get_settings()
    chunks = llm.split_markdown_sections(content, 3500)

    async def run() -> AsyncIterator[dict[str, Any]]:
        model = await llm.resolve_model(settings)
        parts: list[str] = []
        for i, chunk in enumerate(chunks, start=1):
            yield {"type": "status", "message": f"Traduction de la partie {i}/{len(chunks)}…", "part": i, "total": len(chunks)}
            expected = chunk.count("```")
            translated = ""
            for attempt in range(2):
                pieces: list[str] = []
                async for ev in llm.chat_stream(
                    [
                        {"role": "system", "content": prompts.SYSTEM_TRANSLATOR},
                        {"role": "user", "content": prompts.translate_prompt(chunk, i, len(chunks))},
                    ],
                    settings,
                    think=False,
                    temperature=0.2 if attempt == 0 else 0.05,
                    num_predict=llm.estimate_tokens(chunk) * 2 + 400,
                ):
                    if ev["type"] == "token":
                        pieces.append(ev["text"])
                        yield ev
                translated = _strip_wrappers("".join(pieces))
                if translated and translated.count("```") == expected:
                    break  # blocs de code intacts
                yield {"type": "status", "message": f"Partie {i} : structure à revérifier, nouvel essai…"}
            parts.append(translated or chunk)
        title = doc["title"]
        try:
            new_title = _strip_wrappers(
                await llm.chat_once(
                    [{"role": "user", "content": prompts.translate_title_prompt(title)}],
                    settings, think=False, temperature=0.1, num_predict=80,
                )
            ).strip(" «»\"'")
            if new_title and len(new_title) < 200 and "\n" not in new_title:
                title = new_title
        except llm.OllamaError:
            pass
        current = _fresh(db.get_document(doc_id))
        if not current:
            return
        new_data = dict(current["data"]) if isinstance(current.get("data"), dict) else {}
        new_data["translation"] = {
            "from": "en", "to": "fr", "model": model, "date": db.now(),
            "original_title": current["title"], "original_content": current["content"],
        }
        updated = _set_content(doc_id, "\n\n".join(parts) + "\n", title=title, data=new_data)
        live.notify("docs", doc=doc_id, fields=["title", "data"], msg=f"a traduit « {title} » en français")
        yield {"type": "done", "document": _listing(updated or current)}

    return _ndjson(run())


@app.post("/api/documents/{doc_id}/untranslate")
def untranslate_document(doc_id: int) -> dict[str, Any]:
    doc = _get_doc_or_404(doc_id)
    data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
    tr = data.pop("translation", None)
    if not tr:
        raise HTTPException(400, "Ce document n'a pas été traduit.")
    updated = _set_content(doc_id, tr["original_content"], title=tr["original_title"], data=data or None)
    live.notify("docs", doc=doc_id, fields=["title", "data"])
    return _listing(updated or doc)


# =========================================================================== flashcards

class CardIn(BaseModel):
    front: str | None = None
    back: str | None = None


class ReviewIn(BaseModel):
    grade: int = Field(..., ge=0, le=3)


@app.get("/api/documents/{doc_id}/cards")
def document_cards(doc_id: int) -> list[dict[str, Any]]:
    _get_doc_or_404(doc_id)
    return db.list_cards(doc_id)


@app.post("/api/documents/{doc_id}/cards")
def add_card(doc_id: int, body: CardIn) -> list[dict[str, Any]]:
    doc = _get_doc_or_404(doc_id)
    if doc["kind"] != "flashcards":
        raise HTTPException(400, "Ce document n'est pas un paquet de flashcards.")
    if not (body.front or "").strip() or not (body.back or "").strip():
        raise HTTPException(400, "Recto et verso sont obligatoires.")
    db.add_cards(doc_id, [{"front": body.front.strip(), "back": body.back.strip()}])
    _sync_cards_content(doc_id)
    return db.list_cards(doc_id)


@app.patch("/api/cards/{card_id}")
def update_card(card_id: int, body: CardIn) -> dict[str, Any]:
    card = db.get_card(card_id)
    if not card:
        raise HTTPException(404, "Carte introuvable.")
    fields = {k: v.strip() for k, v in body.model_dump(exclude_none=True).items() if v.strip()}
    card = db.update_card(card_id, **fields)
    _sync_cards_content(card["document_id"])
    return card


@app.delete("/api/cards/{card_id}")
def delete_card(card_id: int) -> dict[str, bool]:
    card = db.get_card(card_id)
    if card:
        db.delete_card(card_id)
        _sync_cards_content(card["document_id"])
    return {"ok": True}


@app.get("/api/review")
def review_queue(subject_id: int | None = None, document_id: int | None = None) -> list[dict[str, Any]]:
    cards = db.due_cards(subject_id=subject_id, document_id=document_id)
    for c in cards:
        c["intervals"] = srs.preview_intervals(c)
    return cards


@app.post("/api/cards/{card_id}/review")
def review_card(card_id: int, body: ReviewIn) -> dict[str, Any]:
    card = db.get_card(card_id)
    if not card:
        raise HTTPException(404, "Carte introuvable.")
    updated = db.update_card(card_id, **srs.schedule(card, body.grade))
    updated["intervals"] = srs.preview_intervals(updated)
    live.notify("review")  # progression commune : le nombre de cartes du jour change pour tout le monde
    return updated


# =========================================================================== calendrier

EVENT_KINDS = {"examen", "partiel", "rattrapage", "oral", "tp", "projet", "autre"}
EVENT_LABELS = {
    "examen": "Examen", "partiel": "Partiel", "rattrapage": "Rattrapage", "oral": "Oral",
    "tp": "TP noté", "projet": "Rendu de projet", "autre": "Autre",
}


class EventIn(BaseModel):
    subject_id: int | None = None
    title: str | None = Field(None, max_length=200)
    kind: str | None = None
    date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    start_time: str | None = Field(None, pattern=r"^(\d{2}:\d{2})?$")
    end_time: str | None = Field(None, pattern=r"^(\d{2}:\d{2})?$")
    location: str | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=4000)


def _clean_event(fields: dict[str, Any]) -> dict[str, Any]:
    if "kind" in fields and fields["kind"] not in EVENT_KINDS:
        raise HTTPException(400, "Type d'événement inconnu.")
    for key in ("start_time", "end_time", "location", "notes"):
        if key in fields and not (fields[key] or "").strip():
            fields[key] = None
    if "title" in fields:
        fields["title"] = (fields["title"] or "").strip()
    return fields


@app.get("/api/events")
def events(start: str | None = None, end: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    return db.list_events(start, end, limit)


@app.get("/api/events/upcoming")
def upcoming_events(limit: int = 8) -> list[dict[str, Any]]:
    return db.list_events(start=db.today(), limit=limit)


@app.post("/api/events")
def create_event(body: EventIn) -> dict[str, Any]:
    fields = _clean_event(body.model_dump())
    if not fields.get("date"):
        raise HTTPException(400, "La date est obligatoire.")
    fields["kind"] = fields.get("kind") or "examen"
    if not fields.get("title"):
        subject = next((s for s in db.list_subjects() if s["id"] == fields.get("subject_id")), None)
        fields["title"] = f"{EVENT_LABELS[fields['kind']]}" + (f" — {subject['name']}" if subject else "")
    event = db.create_event(**fields)
    live.notify("events", msg=f"a ajouté « {event['title']} » au calendrier")
    return event


@app.patch("/api/events/{event_id}")
def update_event(event_id: int, body: EventIn) -> dict[str, Any]:
    if not db.get_event(event_id):
        raise HTTPException(404, "Événement introuvable.")
    fields = _clean_event(body.model_dump(exclude_unset=True))
    if "title" in fields and not fields["title"]:
        raise HTTPException(400, "Le titre ne peut pas être vide.")
    event = db.update_event(event_id, **fields) or {}
    live.notify("events")
    return event


@app.delete("/api/events/{event_id}")
def delete_event(event_id: int) -> dict[str, bool]:
    event = db.get_event(event_id)
    db.delete_event(event_id)
    if event:
        live.notify("events", msg=f"a retiré « {event['title']} » du calendrier")
    return {"ok": True}


def _ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


@app.get("/api/events.ics")
def events_ics() -> Response:
    """Export iCalendar, à importer dans Google Agenda, Outlook, le calendrier du téléphone…"""
    from datetime import date as _date, timedelta

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//DeepLearning//Examens//FR", "CALSCALE:GREGORIAN"]
    stamp = db.now().replace("-", "").replace(":", "")
    for ev in db.list_events():
        day = ev["date"].replace("-", "")
        lines += ["BEGIN:VEVENT", f"UID:deeplearning-event-{ev['id']}@localhost", f"DTSTAMP:{stamp}"]
        if ev["start_time"]:
            start = ev["start_time"].replace(":", "")
            end_time = ev["end_time"]
            if not end_time or end_time <= ev["start_time"]:  # sans heure de fin : 1 h par défaut
                h, m = map(int, ev["start_time"].split(":"))
                end_time = f"{min(h + 1, 23):02d}:{m:02d}"
            lines += [f"DTSTART:{day}T{start}00", f"DTEND:{day}T{end_time.replace(':', '')}00"]
        else:
            nxt = (_date.fromisoformat(ev["date"]) + timedelta(days=1)).isoformat().replace("-", "")
            lines += [f"DTSTART;VALUE=DATE:{day}", f"DTEND;VALUE=DATE:{nxt}"]
        lines.append(f"SUMMARY:{_ics_escape(ev['title'])}")
        if ev["location"]:
            lines.append(f"LOCATION:{_ics_escape(ev['location'])}")
        desc = " · ".join(x for x in (EVENT_LABELS.get(ev["kind"], ""), ev["subject_name"] or "", ev["notes"] or "") if x)
        if desc:
            lines.append(f"DESCRIPTION:{_ics_escape(desc)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return Response(
        "\r\n".join(lines) + "\r\n",
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": _content_disposition("attachment", "examens.ics")},
    )


# =========================================================================== QCM & statistiques

class AttemptIn(BaseModel):
    score: int = Field(..., ge=0)
    total: int = Field(..., ge=1)


@app.post("/api/documents/{doc_id}/attempts")
def add_attempt(doc_id: int, body: AttemptIn) -> list[dict[str, Any]]:
    _get_doc_or_404(doc_id)
    db.add_quiz_attempt(doc_id, body.score, body.total)
    return db.quiz_attempts(doc_id)


@app.get("/api/documents/{doc_id}/attempts")
def attempts(doc_id: int) -> list[dict[str, Any]]:
    return db.quiz_attempts(doc_id)


@app.get("/api/stats")
def stats() -> dict[str, Any]:
    return db.stats()
