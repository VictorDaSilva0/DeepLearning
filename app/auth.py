"""Comptes et sessions, quand l'outil est partagé (DL_AUTH=1).

- Mots de passe : scrypt (bibliothèque standard), sel aléatoire, comparaison en temps constant.
- Sessions : jeton aléatoire dans un cookie HttpOnly / SameSite=Strict ; seule son empreinte SHA-256 est
  stockée en base. Durée : config.SESSION_DAYS jours.
- Tentatives de connexion limitées par identifiant et par adresse IP.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request, Response

from . import config, db

log = logging.getLogger("deeplearning.auth")

COOKIE = "dl_session"
MIN_PASSWORD = 8

# Utilisateur fictif quand les comptes sont désactivés (usage local, seul sur le PC).
LOCAL_USER: dict[str, Any] = {"id": 0, "username": "local", "is_admin": 1, "active": 1, "must_change": 0}

# --------------------------------------------------------------------------- mots de passe

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"scrypt${_N}${_R}${_P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(digest)
        got = hashlib.scrypt(
            password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
        return hmac.compare_digest(got, expected)
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password(secrets.token_urlsafe(12))  # pour un temps de réponse identique si le compte n'existe pas

_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sans caractères ambigus (l/1, O/0…)


def temporary_password(length: int = 12) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def check_new_password(password: str, username: str = "") -> None:
    if len(password) < MIN_PASSWORD:
        raise HTTPException(400, f"Le mot de passe doit faire au moins {MIN_PASSWORD} caractères.")
    if username and password.lower() == username.lower():
        raise HTTPException(400, "Le mot de passe ne peut pas être ton identifiant.")


# --------------------------------------------------------------------------- limitation des tentatives

_WINDOW = 15 * 60
_MAX_PER_NAME = 8
_MAX_PER_IP = 25
_failures: dict[str, deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def client_ip(request: Request) -> str:
    # Le port de l'application n'est ouvert qu'en local : seul le tunnel Cloudflare (qui fixe
    # CF-Connecting-IP) ou ce PC peuvent la joindre, l'en-tête est donc fiable.
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")


def _recent(key: str) -> deque[float]:
    q = _failures[key]
    limit = time.monotonic() - _WINDOW
    while q and q[0] < limit:
        q.popleft()
    return q


def check_rate(ip: str, username: str) -> None:
    with _lock:
        if len(_recent("ip:" + ip)) >= _MAX_PER_IP or len(_recent("user:" + username.lower())) >= _MAX_PER_NAME:
            raise HTTPException(429, "Trop de tentatives de connexion. Réessaie dans un quart d'heure.")


def record_failure(ip: str, username: str) -> None:
    with _lock:
        t = time.monotonic()
        _recent("ip:" + ip).append(t)
        _recent("user:" + username.lower()).append(t)


def clear_failures(username: str) -> None:
    with _lock:
        _failures.pop("user:" + username.lower(), None)


# --------------------------------------------------------------------------- sessions

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def start_session(request: Request, response: Response, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now() + timedelta(days=config.SESSION_DAYS)
    db.create_session(_token_hash(token), user_id, expires.isoformat(timespec="seconds"))
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"
    response.set_cookie(
        COOKIE,
        token,
        max_age=config.SESSION_DAYS * 86400,
        httponly=True,
        secure=https,
        samesite="strict",
        path="/",
    )
    return _token_hash(token)


def end_session(request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE)
    if token:
        db.delete_session(_token_hash(token))
    response.delete_cookie(COOKIE, path="/")


def session_hash(request: Request) -> str | None:
    token = request.cookies.get(COOKIE)
    return _token_hash(token) if token else None


def current_user(request: Request) -> dict[str, Any] | None:
    if not config.AUTH_ENABLED:
        return LOCAL_USER
    h = session_hash(request)
    return db.session_user(h) if h else None


def public(user: dict[str, Any]) -> dict[str, Any]:
    return {
        k: (bool(user[k]) if k in {"is_admin", "active", "must_change"} else user[k])
        for k in ("id", "username", "is_admin", "active", "must_change", "created_at", "last_login")
        if k in user
    }


def login(username: str, password: str) -> dict[str, Any] | None:
    user = db.get_user_for_login(username.strip())
    if not user:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, user["password_hash"]) or not user["active"]:
        return None
    return user


# --------------------------------------------------------------------------- premier administrateur

def bootstrap_admin() -> None:
    """Crée le compte administrateur au premier démarrage avec les comptes activés."""
    if not config.AUTH_ENABLED or db.count_users():
        return
    password = config.ADMIN_PASSWORD
    generated = not password
    if generated:
        password = temporary_password(14)
    elif len(password) < MIN_PASSWORD:
        log.warning("ADMIN_PASSWORD fait moins de %d caractères : pense à le changer.", MIN_PASSWORD)
    db.create_user(config.ADMIN_USERNAME, hash_password(password), is_admin=True, must_change=generated)
    if generated:
        log.warning(
            "Compte administrateur créé : identifiant « %s », mot de passe provisoire « %s » (à changer à la "
            "première connexion).",
            config.ADMIN_USERNAME,
            password,
        )
    else:
        log.warning("Compte administrateur « %s » créé (mot de passe : ADMIN_PASSWORD).", config.ADMIN_USERNAME)
