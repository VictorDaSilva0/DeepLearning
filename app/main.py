"""API FastAPI + service de l'interface web."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, config, db, importers, llm, prompts, render, srs

log = logging.getLogger("deeplearning")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="DeepLearning", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")

KINDS = {
    "cours", "note", "fiche", "resume", "exercices", "qcm", "flashcards",
    "explication", "glossaire", "plan", "questions",
}


# =========================================================================== utilitaires

def _get_doc_or_404(doc_id: int) -> dict[str, Any]:
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document introuvable.")
    return doc


def _listing(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if k not in {"content", "data"}} | {"size": len(doc.get("content") or "")}


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


@app.put("/api/settings")
def put_settings(body: SettingsIn) -> dict[str, Any]:
    return db.update_settings(body.model_dump(exclude_none=True))


# =========================================================================== matières

class SubjectIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")


@app.get("/api/subjects")
def subjects() -> list[dict[str, Any]]:
    return db.list_subjects()


@app.post("/api/subjects")
def create_subject(body: SubjectIn) -> dict[str, Any]:
    if not body.name or not body.name.strip():
        raise HTTPException(400, "Le nom de la matière est obligatoire.")
    try:
        return db.create_subject(body.name.strip(), body.color)
    except Exception as exc:
        raise HTTPException(400, "Une matière porte déjà ce nom.") from exc


@app.patch("/api/subjects/{subject_id}")
def update_subject(subject_id: int, body: SubjectIn) -> dict[str, Any]:
    subject = db.update_subject(subject_id, body.name.strip() if body.name else None, body.color)
    if not subject:
        raise HTTPException(404, "Matière introuvable.")
    return subject


@app.delete("/api/subjects/{subject_id}")
def delete_subject(subject_id: int) -> dict[str, bool]:
    db.delete_subject(subject_id)
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
    return db.create_document(
        (body.title or "Nouvelle note").strip() or "Nouvelle note",
        body.content or "",
        kind,
        body.subject_id,
        body.parent_id,
    )


@app.patch("/api/documents/{doc_id}")
def update_document(doc_id: int, body: DocumentIn) -> dict[str, Any]:
    _get_doc_or_404(doc_id)
    fields = body.model_dump(exclude_unset=True)
    if "kind" in fields and fields["kind"] not in KINDS:
        raise HTTPException(400, "Type de document inconnu.")
    if "title" in fields:
        fields["title"] = (fields["title"] or "").strip() or "Sans titre"
    doc = db.update_document(doc_id, **fields)
    return _listing(doc) if doc else {}


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: int) -> dict[str, bool]:
    source = db.delete_document(doc_id)
    if source:
        (config.UPLOAD_DIR / source).unlink(missing_ok=True)
    return {"ok": True}


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
async def import_files(files: list[UploadFile] = File(...), subject_id: str | None = Form(None)) -> list[dict[str, Any]]:
    sid = int(subject_id) if subject_id and subject_id.strip().isdigit() else None
    results: list[dict[str, Any]] = []
    max_bytes = config.MAX_UPLOAD_MB * 1024 * 1024
    for upload in files:
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
        keep_source = ext in {".pdf", ".docx", ".pptx"}
        if not keep_source:
            path.unlink(missing_ok=True)
        doc = db.create_document(
            res.title,
            res.markdown,
            "cours",
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
    return results


# =========================================================================== génération IA

class GenerateIn(BaseModel):
    document_id: int
    task: str
    count: int = Field(10, ge=1, le=60)
    difficulty: str = "moyen"
    instructions: str = Field("", max_length=4000)
    selection: str | None = None


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
        db.update_document(doc_id, content=_cards_markdown(doc["title"], db.list_cards(doc_id)))


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
    use_selection = bool(body.selection and body.selection.strip())
    source_text = body.selection.strip() if use_selection else (doc["content"] or "")
    if not source_text.strip():
        raise HTTPException(400, "Le document est vide : rien à analyser.")
    settings = db.get_settings()
    schema = spec.get("schema")

    async def run() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "status", "message": "Préparation du cours…"}
        source = source_text
        async for ev in llm.fit_source(source_text, settings, spec["reserve"]):
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
                    body.task, doc["title"], source, body.count, body.difficulty, body.instructions.strip()
                ),
            },
        ]
        think = False if schema else None
        thinking_on = bool(settings.get("think")) and think is None
        parts: list[str] = []
        info: dict[str, Any] = {}
        async for ev in llm.chat_stream(
            messages,
            settings,
            fmt=schema,
            num_predict=None if thinking_on else spec["reserve"] + 800,
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
        title = f"{spec['label']} — {doc['title']}" + (" (extrait)" if use_selection else "")
        data = None
        cards: list[dict[str, str]] = []
        if body.task == "qcm":
            questions = _normalize_qcm(llm.parse_json_loose(text))
            if not questions:
                raise ValueError("Le modèle n'a pas produit de questions exploitables. Réessaie.")
            data = {"questions": questions}
            content = _qcm_markdown(doc["title"], questions)
        elif body.task == "flashcards":
            cards = _normalize_cards(llm.parse_json_loose(text))
            if not cards:
                raise ValueError("Le modèle n'a pas produit de cartes exploitables. Réessaie.")
            content = _cards_markdown(title, cards)
        else:
            content = re.sub(r"^```(?:markdown|md)?\s*\n(.*?)\n```\s*$", r"\1", text, flags=re.S)

        new_doc = db.create_document(
            title, content, spec["kind"], subject_id=doc["subject_id"], parent_id=doc["id"], data=data
        )
        if cards:
            db.add_cards(new_doc["id"], cards)
        warning = None
        if info.get("done_reason") == "length":
            warning = "La réponse a été coupée (limite de longueur atteinte). Augmente le contexte dans les réglages."
        yield {"type": "done", "document": _listing(new_doc), "info": info, "warning": warning}

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

    system = prompts.SYSTEM_TUTOR
    if body.document_id is not None:
        doc = _get_doc_or_404(body.document_id)
        budget = llm.input_budget_chars(settings, 2500) - sum(len(m.content) for m in kept)
        query = " ".join(m.content for m in kept if m.role == "user")[-2000:]
        context, partial = llm.select_passages(doc["content"] or "", query, max(3000, budget))
        system = prompts.chat_system_prompt(doc["title"], context, partial)

    messages = [{"role": "system", "content": system}] + [{"role": m.role, "content": m.content} for m in kept]

    async def run() -> AsyncIterator[dict[str, Any]]:
        async for ev in llm.chat_stream(messages, settings):
            yield ev

    return _ndjson(run())


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
            current = db.get_document(doc_id)
            if not current:
                return
            new_content = re.sub(
                rf"^> 📷 \*\*Page {page}\*\*.*$",
                lambda _m: f"*(Page {page} — transcrite par l'IA)*\n\n{text}",
                current["content"],
                count=1,
                flags=re.MULTILINE,
            )
            db.update_document(doc_id, content=new_content)
            yield {"type": "page_done", "page": page}
        yield {"type": "done", "document": _listing(db.get_document(doc_id) or doc)}

    return _ndjson(run())


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
    return updated


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
