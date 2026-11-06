"""Client Ollama (API HTTP locale) et outils de gestion du contexte."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from typing import Any, AsyncIterator

import httpx

from . import config, db, prompts

# Estimation grossière du nombre de caractères par token pour du français.
CHARS_PER_TOKEN = 3.3


class OllamaError(Exception):
    pass


_caps_cache: dict[str, list[str]] = {}


def _client(timeout: float = 900) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=config.OLLAMA_URL, timeout=httpx.Timeout(timeout, connect=5))


def _connection_error(exc: Exception) -> OllamaError:
    return OllamaError(
        f"Impossible de joindre Ollama ({config.OLLAMA_URL}). Vérifie que l'application Ollama est lancée. "
        f"[{type(exc).__name__}]"
    )


async def version() -> str | None:
    try:
        async with _client(5) as c:
            r = await c.get("/api/version")
            r.raise_for_status()
            return r.json().get("version")
    except Exception:
        return None


async def list_models() -> list[dict[str, Any]]:
    try:
        async with _client(10) as c:
            r = await c.get("/api/tags")
            r.raise_for_status()
    except httpx.HTTPError as exc:
        raise _connection_error(exc) from exc
    models = []
    for m in r.json().get("models", []):
        details = m.get("details") or {}
        models.append(
            {
                "name": m.get("name") or m.get("model"),
                "size": m.get("size", 0),
                "parameter_size": details.get("parameter_size", ""),
                "family": details.get("family", ""),
            }
        )
    models.sort(key=lambda m: m["name"])
    return models


async def capabilities(model: str) -> list[str]:
    if model in _caps_cache:
        return _caps_cache[model]
    try:
        async with _client(15) as c:
            r = await c.post("/api/show", json={"model": model})
            r.raise_for_status()
            caps = r.json().get("capabilities") or ["completion"]
    except httpx.HTTPError:
        caps = ["completion"]
    _caps_cache[model] = caps
    return caps


async def resolve_model(settings: dict[str, Any]) -> str:
    models = await list_models()
    names = [m["name"] for m in models]
    wanted = settings.get("model") or ""
    if wanted and wanted in names:
        return wanted
    usable = [n for n in names if "embed" not in n.lower()]
    if not usable:
        raise OllamaError("Aucun modèle n'est installé dans Ollama. Exemple : `ollama pull gemma3:12b`.")
    if wanted:
        # Le modèle choisi a disparu : on se rabat sur le premier disponible.
        return usable[0]
    return usable[0]


async def chat_stream(
    messages: list[dict[str, Any]],
    settings: dict[str, Any],
    *,
    fmt: Any = None,
    num_predict: int | None = None,
    think: bool | None = None,
    temperature: float | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Diffuse la réponse du modèle : événements {type: token|thinking|done}."""
    model = await resolve_model(settings)
    caps = await capabilities(model)
    options: dict[str, Any] = {
        "num_ctx": int(settings.get("num_ctx") or 16384),
        "temperature": float(settings.get("temperature") if temperature is None else temperature),
    }
    if num_predict:
        options["num_predict"] = int(num_predict)
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": options,
        "keep_alive": "30m",
    }
    if fmt is not None:
        payload["format"] = fmt
    if "thinking" in caps:
        payload["think"] = bool(settings.get("think")) if think is None else bool(think)

    try:
        async with _client() as c:
            async with c.stream("POST", "/api/chat", json=payload) as resp:
                if resp.status_code != 200:
                    raw = (await resp.aread()).decode("utf-8", errors="replace")
                    try:
                        detail = json.loads(raw).get("error", raw)
                    except json.JSONDecodeError:
                        detail = raw
                    raise OllamaError(f"Ollama a renvoyé une erreur ({resp.status_code}) : {detail}")
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise OllamaError(str(chunk["error"]))
                    msg = chunk.get("message") or {}
                    if msg.get("thinking"):
                        yield {"type": "thinking", "text": msg["thinking"]}
                    if msg.get("content"):
                        yield {"type": "token", "text": msg["content"]}
                    if chunk.get("done"):
                        yield {
                            "type": "done",
                            "model": model,
                            "done_reason": chunk.get("done_reason"),
                            "eval_count": chunk.get("eval_count"),
                            "prompt_eval_count": chunk.get("prompt_eval_count"),
                        }
                        return
    except httpx.TimeoutException as exc:
        raise OllamaError("Ollama met trop de temps à répondre (délai dépassé).") from exc
    except httpx.TransportError as exc:
        raise _connection_error(exc) from exc


async def chat_once(messages: list[dict[str, Any]], settings: dict[str, Any], **kwargs: Any) -> str:
    parts = []
    async for ev in chat_stream(messages, settings, **kwargs):
        if ev["type"] == "token":
            parts.append(ev["text"])
    return "".join(parts)


# =========================================================================== budget de contexte

def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def input_budget_chars(settings: dict[str, Any], reserve_tokens: int) -> int:
    num_ctx = int(settings.get("num_ctx") or 16384)
    budget_tokens = num_ctx - reserve_tokens - 900
    return max(3000, int(budget_tokens * CHARS_PER_TOKEN))


def split_text(text: str, max_chars: int) -> list[str]:
    """Découpe en morceaux d'au plus max_chars, en respectant si possible les paragraphes."""
    paragraphs = re.split(r"\n\s*\n", text)
    pieces: list[str] = []
    for p in paragraphs:
        if len(p) <= max_chars:
            pieces.append(p)
            continue
        # Paragraphe géant : on coupe par phrases puis brutalement si besoin.
        sentences = re.split(r"(?<=[.!?])\s+|\n", p)
        buf = ""
        for s in sentences:
            while len(s) > max_chars:
                pieces.append(s[:max_chars])
                s = s[max_chars:]
            if len(buf) + len(s) + 1 > max_chars and buf:
                pieces.append(buf)
                buf = s
            else:
                buf = f"{buf} {s}" if buf else s
        if buf:
            pieces.append(buf)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > max_chars:
            chunks.append(current)
            current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


async def fit_source(
    text: str,
    settings: dict[str, Any],
    reserve_tokens: int,
    budget: int | None = None,
    label: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Condense le cours s'il est trop long pour le contexte (ou pour `budget` caractères, quand plusieurs
    cours se partagent le contexte). Émet des statuts puis {type: source}."""
    budget = budget or input_budget_chars(settings, reserve_tokens)
    what = f"« {label} »" if label else "Cours long"
    if len(text) <= budget:
        yield {"type": "source", "text": text, "condensed": False}
        return

    model = await resolve_model(settings)
    key = hashlib.sha256(f"{model}|{settings.get('num_ctx')}|{budget}|{text}".encode("utf-8")).hexdigest()
    cached = db.cache_get(key)
    if cached:
        yield {"type": "status", "message": f"{what} : version condensée récupérée depuis le cache."}
        yield {"type": "source", "text": cached, "condensed": True}
        return

    num_ctx = int(settings.get("num_ctx") or 16384)
    chunk_chars = max(4000, int((num_ctx - 1500) * 0.6 * CHARS_PER_TOKEN))
    current = text
    for round_no in range(1, 4):
        if len(current) <= budget:
            break
        chunks = split_text(current, chunk_chars)
        target_chars = max(900, int(budget * 0.9 / len(chunks)))
        words = max(120, target_chars // 6)
        parts = []
        for i, chunk in enumerate(chunks, start=1):
            yield {
                "type": "status",
                "message": f"{what} : lecture et condensation de la partie {i}/{len(chunks)}"
                + (f" (passe {round_no})" if round_no > 1 else "")
                + "…",
            }
            summary = await chat_once(
                [
                    {"role": "system", "content": prompts.SYSTEM_CONDENSE},
                    {"role": "user", "content": prompts.condense_prompt(i, len(chunks), words, chunk)},
                ],
                settings,
                num_predict=int(target_chars / CHARS_PER_TOKEN * 1.6) + 200,
                think=False,
                temperature=0.2,
            )
            parts.append(summary.strip())
        current = "\n\n".join(parts)

    if len(current) > budget:
        current = current[:budget] + "\n\n[…texte tronqué pour tenir dans le contexte du modèle…]"
    db.cache_set(key, current)
    yield {"type": "source", "text": current, "condensed": True}


# =========================================================================== langue & découpage

_EN_WORDS = set("the and of to is are in that for with on this be can from by an as or it which you your not use".split())
_FR_WORDS = set("le la les des et est une un du dans pour que qui sur pas avec au aux ce sont vous nous par".split())


def detect_language(text: str) -> str | None:
    """« en » / « fr » d'après les mots les plus courants (hors blocs de code), sinon None."""
    sample = re.sub(r"```.*?```|`[^`]*`", " ", text[:20000], flags=re.S).lower()
    words = re.findall(r"[a-zàâçéèêëîïôûùüÿœ']+", sample)
    en = sum(1 for w in words if w in _EN_WORDS)
    fr = sum(1 for w in words if w in _FR_WORDS)
    if en >= 15 and en > 2 * fr:
        return "en"
    if fr >= 15 and fr > 2 * en:
        return "fr"
    return None


def split_markdown_sections(text: str, max_chars: int = 3500) -> list[str]:
    """Découpe un Markdown en morceaux d'au plus ~max_chars, aux titres « ## » / « ### »,
    sans jamais couper un bloc de code."""
    sections: list[str] = []
    current: list[str] = []
    in_code = False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
        if not in_code and re.match(r"#{1,3} ", line) and current:
            sections.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        sections.append("\n".join(current))

    chunks: list[str] = []
    buf = ""
    for sec in sections:
        if buf and len(buf) + len(sec) + 2 > max_chars:
            chunks.append(buf)
            buf = sec
        else:
            buf = f"{buf}\n\n{sec}" if buf else sec
    if buf:
        chunks.append(buf)
    return [c.strip("\n") for c in chunks if c.strip()]


# =========================================================================== recherche de passages (chat)

_STOPWORDS = set(
    "le la les un une des du de d l et ou en au aux a à ce cet cette ces se sa son ses sur dans par pour pas "
    "plus que qui quoi quel quelle quels quelles est sont être avoir il elle ils elles on nous vous je tu me te "
    "lui leur y ne n s c qu mais donc or ni car comme avec sans sous entre vers chez tout tous toute toutes "
    "the of and to in is are what how why".split()
)


def _terms(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return [t for t in re.findall(r"[a-z0-9]{2,}", text) if t not in _STOPWORDS]


def select_passages(text: str, query: str, budget_chars: int) -> tuple[str, bool]:
    """Sélectionne les passages les plus pertinents (BM25 simplifié). Renvoie (contexte, partiel)."""
    if len(text) <= budget_chars:
        return text, False
    passages = split_text(text, 1200)
    docs = [_terms(p) for p in passages]
    q_terms = set(_terms(query))
    n = len(passages)
    avg_len = sum(len(d) for d in docs) / max(n, 1)
    df = Counter(t for d in docs for t in set(d))
    scores = []
    for idx, d in enumerate(docs):
        tf = Counter(d)
        score = 0.0
        for t in q_terms:
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(d) / max(avg_len, 1)))
        scores.append((score, idx))
    scores.sort(key=lambda x: (-x[0], x[1]))

    chosen: list[int] = [0]  # le début du cours donne souvent le contexte général
    total = len(passages[0])
    for score, idx in scores:
        if idx in chosen:
            continue
        if total + len(passages[idx]) > budget_chars:
            continue
        chosen.append(idx)
        total += len(passages[idx])
    chosen.sort()
    return "\n\n[…]\n\n".join(passages[i] for i in chosen), True


# =========================================================================== JSON tolérant

_LATEX_AFTER_N = re.compile(
    r"(?:eq|abla|u|ot|otin|e|i|eg|ewline|exists|geq|leq|mid|parallel|subseteq|supseteq)(?![A-Za-z])"
)


def fix_latex_escapes(raw: str) -> str:
    """Le modèle écrit souvent du LaTeX (\\frac, \\text, \\rightarrow…) dans le JSON sans doubler
    les antislashs : « \\t » devient alors une tabulation, « \\r » un retour chariot, etc.
    On double les antislashs qui introduisent manifestement une commande LaTeX."""

    def repl(m: re.Match[str]) -> str:
        nxt = m.group(1)
        if nxt in '\\"/':
            return m.group(0)
        rest = raw[m.end() : m.end() + 12]
        if nxt in "tbfr" and rest[:1].isalpha() and rest[:1].islower():
            return "\\\\" + nxt
        if nxt == "n" and _LATEX_AFTER_N.match(rest):
            return "\\\\" + nxt
        if nxt == "u" and not re.match(r"[0-9a-fA-F]{4}", rest):
            return "\\\\" + nxt
        if nxt in "bfnrtu":
            return m.group(0)
        return "\\\\" + nxt  # échappement JSON invalide (\alpha, \(, \[…) : on garde l'antislash

    return re.sub(r"\\(.)", repl, raw, flags=re.S)


def parse_json_loose(text: str) -> Any:
    """Parse du JSON éventuellement entouré de ```, tronqué, ou contenant du LaTeX mal échappé."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    text = fix_latex_escapes(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        raise ValueError("Réponse sans JSON.")
    positions = [m.start() for m in re.finditer(r"}", text)][::-1][:300]
    for pos in positions:
        candidate = text[start : pos + 1]
        for suffix in ("", "]}", "}]}", "]}]}", "}"):
            try:
                return json.loads(candidate + suffix)
            except json.JSONDecodeError:
                continue
    raise ValueError("JSON invalide renvoyé par le modèle.")
