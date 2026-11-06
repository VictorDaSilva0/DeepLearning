"""Temps réel : qui est connecté (et où), modifications des autres en direct, édition à plusieurs.

Un seul canal WebSocket par onglet (/api/live). Le serveur garde en mémoire la version de référence de
chaque document ouvert : les modifications de texte arrivent sous forme d'opérations, transformées entre
elles quand deux personnes écrivent en même temps (transformation opérationnelle, comme ot.js), puis
renvoyées aux autres lecteurs et enregistrées peu après dans SQLite.

Opération de texte = liste de composantes : entier > 0 → garder n caractères, chaîne → insérer,
entier < 0 → supprimer n caractères. Les longueurs sont en unités UTF-16, comme les chaînes JavaScript.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import secrets
from typing import Any, Callable
from urllib.parse import urlparse

from starlette.websockets import WebSocket, WebSocketDisconnect

from . import auth, db

log = logging.getLogger("deeplearning.live")

COLORS = [
    "#e5484d", "#0090ff", "#46a758", "#f76b15", "#8e4ec6", "#12a594",
    "#d6409f", "#ffb224", "#3e63dd", "#ab4aba", "#29a383", "#e54666",
]
VIEWS = {"home", "doc", "calendar", "review"}
HISTORY = 500          # opérations gardées par document (pour transformer les modifications concurrentes)
SAVE_DELAY = 0.6       # s entre une modification et son enregistrement
GRACE = 300            # s pendant lesquelles un document reste en mémoire après le départ du dernier lecteur
TYPING = 4.0           # s : « en train d'écrire » après la dernière frappe
MAX_UNITS = 5_000_000  # taille maximale d'un document (unités UTF-16)
MAX_QUEUE = 5000       # messages en attente pour un onglet trop lent → déconnexion

# Utilisateur de la requête HTTP en cours (fixé par main.py) : l'auteur des modifications annoncées.
CURRENT_USER: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("live_user", default=None)


def color_for(user_id: int) -> str:
    return COLORS[user_id % len(COLORS)]


# =========================================================================== opérations de texte

def ulen(text: str) -> int:
    """Longueur en unités UTF-16 (comme `str.length` en JavaScript)."""
    return len(text.encode("utf-16-le")) // 2


def _push(op: list, c: int | str) -> None:
    """Ajoute une composante en gardant la forme canonique (fusion, insertion avant suppression)."""
    if isinstance(c, str):
        if not c:
            return
        if op and isinstance(op[-1], str):
            op[-1] += c
        elif op and op[-1] < 0:
            if len(op) >= 2 and isinstance(op[-2], str):
                op[-2] += c
            else:
                op.insert(len(op) - 1, c)
        else:
            op.append(c)
    elif c:
        if op and not isinstance(op[-1], str) and (op[-1] > 0) == (c > 0):
            op[-1] += c
        else:
            op.append(c)


def clean_op(raw: Any) -> list | None:
    if not isinstance(raw, list) or len(raw) > 20000:
        return None
    op: list = []
    for c in raw:
        if isinstance(c, bool) or not isinstance(c, (int, str)):
            return None
        _push(op, c)
    return op


def base_len(op: list) -> int:
    return sum(abs(c) for c in op if not isinstance(c, str))


def apply(text: str, op: list) -> str:
    src = text.encode("utf-16-le")
    out: list[bytes] = []
    i = 0
    for c in op:
        if isinstance(c, str):
            out.append(c.encode("utf-16-le", "surrogatepass"))
        elif c > 0:
            out.append(src[i:i + 2 * c])
            i += 2 * c
        else:
            i -= 2 * c
        if i > len(src):
            raise ValueError("opération plus longue que le texte")
    if i != len(src):
        raise ValueError("l'opération ne couvre pas tout le texte")
    return b"".join(out).decode("utf-16-le")  # strict : refuse un émoji coupé en deux


def transform(op1: list, op2: list) -> tuple[list, list]:
    """op1 et op2 partent du même texte → (op1', op2') tels que op1 puis op2' = op2 puis op1'.
    À position égale, les insertions de op1 passent en premier."""
    if base_len(op1) != base_len(op2):
        raise ValueError("opérations sur des textes différents")
    p1: list = []
    p2: list = []
    it1, it2 = iter(op1), iter(op2)
    a, b = next(it1, None), next(it2, None)
    while a is not None or b is not None:
        if isinstance(a, str):
            _push(p1, a)
            _push(p2, ulen(a))
            a = next(it1, None)
            continue
        if isinstance(b, str):
            _push(p1, ulen(b))
            _push(p2, b)
            b = next(it2, None)
            continue
        if a is None or b is None:
            raise ValueError("opérations incompatibles")
        if a > 0 and b > 0:
            m = min(a, b)
            _push(p1, m)
            _push(p2, m)
            a, b = (a - m) or next(it1, None), (b - m) or next(it2, None)
        elif a < 0 and b < 0:
            m = min(-a, -b)
            a, b = (a + m) or next(it1, None), (b + m) or next(it2, None)
        elif a < 0:  # a supprime, b garde
            m = min(-a, b)
            _push(p1, -m)
            a, b = (a + m) or next(it1, None), (b - m) or next(it2, None)
        else:  # a garde, b supprime
            m = min(a, -b)
            _push(p2, -m)
            a, b = (a - m) or next(it1, None), (b + m) or next(it2, None)
    return p1, p2


def transform_index(op: list, index: int) -> int:
    """Position d'un curseur après l'opération."""
    new = index
    for c in op:
        if isinstance(c, str):
            new += ulen(c)
        elif c > 0:
            index -= c
        else:
            new -= min(index, -c)
            index += c
        if index < 0:
            break
    return new


def diff_op(old: str, new: str) -> list:
    """Opération qui transforme old en new (une seule zone modifiée)."""
    n = min(len(old), len(new))
    p = 0
    while p < n and old[p] == new[p]:
        p += 1
    s = 0
    while s < n - p and old[-1 - s] == new[-1 - s]:
        s += 1
    op: list = []
    _push(op, ulen(old[:p]))
    _push(op, new[p:len(new) - s])
    _push(op, -ulen(old[p:len(old) - s]))
    _push(op, ulen(old[len(old) - s:]))
    return op


# =========================================================================== état partagé

class Client:
    def __init__(self, ws: WebSocket, user: dict[str, Any], key: str) -> None:
        self.ws = ws
        self.user = user
        self.key = key  # identifiant de l'onglet, conservé d'une reconnexion à l'autre
        self.cid = secrets.token_hex(5)
        self.view = "home"
        self.doc_id: int | None = None
        self.typing: int | None = None
        self.typing_handle: asyncio.TimerHandle | None = None
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.writer: asyncio.Task | None = None

    @property
    def public(self) -> dict[str, Any]:
        uid = int(self.user["id"])
        return {"cid": self.cid, "id": uid, "name": self.user["username"], "color": color_for(uid)}


class LiveDoc:
    def __init__(self, doc_id: int, content: str) -> None:
        self.id = doc_id
        self.content = content
        self.units = ulen(content)
        self.rev = 0
        self.history: list[list] = []  # history[-k] = opération qui a mené à rev - k + 1
        self.seqs: dict[str, int] = {}  # dernière opération appliquée pour chaque onglet
        self.members: set[str] = set()
        self.dirty = False
        self.deleted = False
        self.saver: asyncio.Task | None = None
        self.unload: asyncio.TimerHandle | None = None


clients: dict[str, Client] = {}
docs: dict[int, LiveDoc] = {}
_loading: dict[int, asyncio.Future] = {}
_loop: asyncio.AbstractEventLoop | None = None
_presence_handle: asyncio.TimerHandle | None = None


def _enqueue(client: Client, text: str) -> None:
    if client.queue.qsize() > MAX_QUEUE:
        # Onglet qui ne lit plus (connexion figée) : on coupe, il se reconnectera et repartira à jour.
        if client.writer and not client.writer.done():
            log.warning("Onglet trop lent, déconnecté : %s", client.user.get("username"))
            client.writer.cancel()
        return
    client.queue.put_nowait(text)


def _send(client: Client, payload: dict[str, Any]) -> None:
    _enqueue(client, json.dumps(payload, ensure_ascii=False))


def _broadcast(payload: dict[str, Any], *, to: Callable[[Client], bool] | None = None) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    for c in list(clients.values()):
        if to is None or to(c):
            _enqueue(c, text)


def _presence_now() -> None:
    global _presence_handle
    _presence_handle = None
    users = [
        c.public | {"view": c.view, "doc": c.doc_id, "typing": c.typing}
        for c in clients.values()
    ]
    _broadcast({"t": "presence", "users": users})


def _presence_changed() -> None:
    """Annonce la présence de tous (regroupé : plusieurs changements dans la même fraction de seconde)."""
    global _presence_handle
    if _presence_handle is None and _loop is not None:
        _presence_handle = _loop.call_later(0.05, _presence_now)


def _in_loop(fn: Callable[..., Any], *args: Any) -> None:
    """Exécute fn dans la boucle asyncio du temps réel, depuis n'importe quel fil (routes synchrones)."""
    loop = _loop
    if loop is None or loop.is_closed():
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        fn(*args)
    else:
        loop.call_soon_threadsafe(fn, *args)


# =========================================================================== documents ouverts

async def _load(doc_id: int) -> LiveDoc | None:
    if doc_id in docs:
        return docs[doc_id]
    fut = _loading.get(doc_id)
    if fut is None:
        fut = asyncio.get_running_loop().create_future()
        _loading[doc_id] = fut
        try:
            row = await asyncio.to_thread(db.get_document, doc_id)
            ld = None
            if row:
                ld = docs.get(doc_id) or LiveDoc(doc_id, row["content"] or "")
                docs[doc_id] = ld
            fut.set_result(ld)
        except Exception as exc:  # pragma: no cover - base indisponible
            fut.set_exception(exc)
        finally:
            _loading.pop(doc_id, None)
    return await asyncio.shield(fut)


def _schedule_save(ld: LiveDoc) -> None:
    ld.dirty = True
    if ld.saver is None or ld.saver.done():
        ld.saver = asyncio.get_running_loop().create_task(_saver(ld))


async def _saver(ld: LiveDoc) -> None:
    while ld.dirty and not ld.deleted:
        await asyncio.sleep(SAVE_DELAY)
        if ld.deleted:
            return
        ld.dirty = False
        try:
            await asyncio.to_thread(db.update_document, ld.id, content=ld.content)
        except Exception:
            log.exception("Enregistrement du document %s impossible", ld.id)
            ld.dirty = True
            await asyncio.sleep(3)


def _maybe_unload(ld: LiveDoc) -> None:
    ld.unload = None
    if ld.members or docs.get(ld.id) is not ld:
        return
    if ld.dirty or (ld.saver and not ld.saver.done()):
        ld.unload = asyncio.get_running_loop().call_later(5, _maybe_unload, ld)
        return
    del docs[ld.id]


def _leave_doc(client: Client) -> None:
    ld = docs.get(client.doc_id) if client.doc_id is not None else None
    if client.typing is not None:
        client.typing = None
    if ld:
        ld.members.discard(client.cid)
        _broadcast({"t": "caret", "doc": ld.id, "cid": client.cid, "a": None},
                   to=lambda c: c.cid in ld.members)
        if not ld.members and ld.unload is None:
            ld.unload = asyncio.get_running_loop().call_later(GRACE, _maybe_unload, ld)


def _joined(client: Client, ld: LiveDoc) -> dict[str, Any]:
    return {"t": "joined", "doc": ld.id, "rev": ld.rev, "content": ld.content, "seq": ld.seqs.get(client.key)}


def _commit(ld: LiveDoc, op: list, new_text: str) -> None:
    ld.content = new_text
    ld.units = ulen(new_text)
    ld.rev += 1
    ld.history.append(op)
    if len(ld.history) > HISTORY:
        del ld.history[: len(ld.history) - HISTORY]


def _set_typing(client: Client, doc_id: int | None) -> None:
    if client.typing_handle:
        client.typing_handle.cancel()
        client.typing_handle = None
    if client.typing != doc_id:
        client.typing = doc_id
        _presence_changed()
    if doc_id is not None and _loop is not None:
        client.typing_handle = _loop.call_later(TYPING, _set_typing, client, None)


# =========================================================================== messages

async def _on_where(client: Client, msg: dict[str, Any]) -> None:
    view = msg.get("view") if msg.get("view") in VIEWS else "home"
    doc_id = msg.get("doc") if view == "doc" and isinstance(msg.get("doc"), int) else None
    if doc_id != client.doc_id:
        _leave_doc(client)
        client.doc_id = None
    client.view = view
    if doc_id is not None:
        ld = await _load(doc_id)
        if client.cid not in clients:
            return
        if not ld:
            _send(client, {"t": "gone", "doc": doc_id})
        else:
            client.doc_id = doc_id
            ld.members.add(client.cid)
            if ld.unload:
                ld.unload.cancel()
                ld.unload = None
            _send(client, _joined(client, ld))
    _presence_changed()


def _on_op(client: Client, msg: dict[str, Any]) -> None:
    doc_id, rev, seq = msg.get("doc"), msg.get("rev"), msg.get("seq")
    ld = docs.get(doc_id) if isinstance(doc_id, int) else None
    if not ld or client.cid not in ld.members or not isinstance(rev, int) or not isinstance(seq, int):
        return
    op = clean_op(msg.get("op"))
    if op is None or rev > ld.rev or rev < ld.rev - len(ld.history) or seq <= ld.seqs.get(client.key, 0):
        _send(client, _joined(client, ld))  # désynchronisé : on renvoie la version de référence
        return
    try:
        for done in ld.history[len(ld.history) - (ld.rev - rev):] if ld.rev > rev else []:
            op, _ = transform(op, done)
        new_text = apply(ld.content, op)
    except (ValueError, UnicodeDecodeError):
        _send(client, _joined(client, ld))
        return
    if ulen(new_text) > MAX_UNITS:
        _send(client, {"t": "error", "message": "Document trop long."})
        _send(client, _joined(client, ld))
        return
    _commit(ld, op, new_text)
    ld.seqs[client.key] = seq
    _send(client, {"t": "ack", "doc": ld.id, "rev": ld.rev})
    _broadcast({"t": "op", "doc": ld.id, "rev": ld.rev, "op": op, "cid": client.cid},
               to=lambda c: c.cid in ld.members and c is not client)
    _schedule_save(ld)
    _set_typing(client, ld.id)


def _on_caret(client: Client, msg: dict[str, Any]) -> None:
    doc_id, rev = msg.get("doc"), msg.get("rev")
    ld = docs.get(doc_id) if isinstance(doc_id, int) else None
    if not ld or client.cid not in ld.members:
        return
    a, b = msg.get("a"), msg.get("b")
    out: dict[str, Any] = {"t": "caret", "doc": ld.id, "cid": client.cid, "rev": ld.rev, "a": None}
    if isinstance(a, int) and isinstance(b, int) and isinstance(rev, int) and not isinstance(a, bool):
        if rev > ld.rev or rev < ld.rev - len(ld.history):
            return
        for done in ld.history[len(ld.history) - (ld.rev - rev):] if ld.rev > rev else []:
            a, b = transform_index(done, a), transform_index(done, b)
        out["a"], out["b"] = max(0, min(a, ld.units)), max(0, min(b, ld.units))
    _broadcast(out, to=lambda c: c.cid in ld.members and c is not client)


async def _handle(client: Client, msg: dict[str, Any]) -> None:
    t = msg.get("t")
    if t == "op":
        _on_op(client, msg)
    elif t == "caret":
        _on_caret(client, msg)
    elif t == "where":
        await _on_where(client, msg)
    elif t == "sync":
        ld = docs.get(client.doc_id) if client.doc_id is not None else None
        if ld and msg.get("doc") == ld.id:
            _send(client, _joined(client, ld))
    elif t == "chat_typing":  # « Léa écrit… » dans le chat (fil général ou d'un document)
        doc = msg.get("doc") if isinstance(msg.get("doc"), int) else None
        _broadcast({"t": "chat_typing", "doc": doc} | client.public, to=lambda c: c is not client)
    elif t == "ping":
        _send(client, {"t": "pong"})


async def _writer(client: Client) -> None:
    try:
        while True:
            text = await client.queue.get()
            if text is None:
                break
            await client.ws.send_text(text)
    except Exception:
        pass
    finally:
        try:
            await client.ws.close()
        except Exception:
            pass


def _same_origin(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    if not origin:
        return True  # pas un navigateur
    host = ws.headers.get("x-forwarded-host") or ws.headers.get("host") or ""
    return urlparse(origin).netloc.lower() == host.lower()


async def serve(ws: WebSocket) -> None:
    """Point d'entrée du canal /api/live."""
    global _loop
    _loop = asyncio.get_running_loop()
    if not _same_origin(ws):
        await ws.close(code=4403)
        return
    user = await asyncio.to_thread(auth.current_user, ws)
    await ws.accept()
    if not user or user.get("must_change") or not user.get("active", 1):
        await ws.close(code=4401)
        return
    key = (ws.query_params.get("key") or "")[:40] or secrets.token_hex(8)
    client = Client(ws, user, key)
    clients[client.cid] = client
    writer = client.writer = asyncio.create_task(_writer(client))
    _send(client, {"t": "welcome", "me": client.public})
    _presence_changed()
    try:
        while True:
            raw = await ws.receive_text()
            if len(raw) > 8_000_000:
                break
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict):
                await _handle(client, msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("Canal temps réel interrompu")
    finally:
        clients.pop(client.cid, None)
        if client.typing_handle:
            client.typing_handle.cancel()
        _leave_doc(client)
        client.queue.put_nowait(None)
        _presence_changed()
        await asyncio.wait({writer}, timeout=2)
        if not writer.done():
            writer.cancel()


# =========================================================================== appels depuis les routes HTTP

def content(doc_id: int) -> str | None:
    """Texte en cours d'édition (peut être en avance de quelques dixièmes de seconde sur la base)."""
    ld = docs.get(doc_id)
    return ld.content if ld and not ld.deleted else None


def _replace(doc_id: int, new_text: str) -> None:
    ld = docs.get(doc_id)
    if not ld or ld.deleted or ld.content == new_text:
        return
    op = diff_op(ld.content, new_text)
    _commit(ld, op, new_text)
    _broadcast({"t": "op", "doc": ld.id, "rev": ld.rev, "op": op, "cid": None}, to=lambda c: c.cid in ld.members)
    _schedule_save(ld)  # réécrit la même version : évite qu'un enregistrement en cours ne l'écrase


def replace_content(doc_id: int, new_text: str) -> None:
    """Le texte a été modifié hors de l'éditeur (traduction, OCR, flashcards…) : on l'envoie aux lecteurs."""
    _in_loop(_replace, doc_id, new_text)


def _forget(doc_id: int, by: dict[str, Any] | None) -> None:
    ld = docs.pop(doc_id, None)
    if ld:
        ld.deleted = True
        if ld.saver:
            ld.saver.cancel()
        if ld.unload:
            ld.unload.cancel()
    _broadcast({"t": "gone", "doc": doc_id, "by": by}, to=lambda c: c.doc_id == doc_id)


def forget(doc_id: int) -> None:
    """Document supprimé."""
    _in_loop(_forget, doc_id, _author())


def _author() -> dict[str, Any] | None:
    user = CURRENT_USER.get()
    if not user:
        return None
    uid = int(user["id"])
    return {"id": uid, "name": user["username"], "color": color_for(uid)}


def notify(what: str, *, doc: int | None = None, msg: str | None = None, fields: list[str] | None = None) -> None:
    """Annonce une modification (bibliothèque, calendrier, flashcards…) à tous les onglets ouverts."""
    payload = {"t": "changed", "what": what, "doc": doc, "msg": msg, "fields": fields or [], "by": _author()}
    _in_loop(_broadcast, payload)


def chat_message(message: dict[str, Any]) -> None:
    """Nouveau message de chat : envoyé à tous (pour le fil ouvert et les compteurs de non-lus)."""
    _in_loop(_broadcast, {"t": "chat", "message": message})


def chat_deleted(message: dict[str, Any]) -> None:
    _in_loop(_broadcast, {"t": "chat_deleted", "id": message["id"], "doc": message["document_id"]})


def _disconnect(user_id: int) -> None:
    for c in list(clients.values()):
        if int(c.user["id"]) == user_id:
            c.queue.put_nowait(None)


def disconnect_user(user_id: int) -> None:
    """Compte désactivé, supprimé ou mot de passe réinitialisé : ses onglets sont déconnectés."""
    _in_loop(_disconnect, user_id)


async def shutdown() -> None:
    """Arrêt du serveur : enregistre tout ce qui est en attente."""
    for ld in list(docs.values()):
        if ld.saver:
            ld.saver.cancel()
        if ld.dirty and not ld.deleted:
            try:
                await asyncio.to_thread(db.update_document, ld.id, content=ld.content)
            except Exception:
                log.exception("Enregistrement du document %s impossible", ld.id)
    for c in list(clients.values()):
        c.queue.put_nowait(None)
