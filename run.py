"""Lance DeepLearning et ouvre l'interface dans le navigateur.

Usage :  python run.py [--no-browser] [--no-reload]

- Si DeepLearning tourne déjà avec le code à jour, on ouvre simplement le navigateur.
- S'il tourne encore sur une ancienne version du code, il est arrêté et relancé.
- Le serveur se recharge tout seul quand le code Python change (désactivable avec --no-reload).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

import uvicorn

from app import config


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def fetch(url: str, timeout: float = 3) -> tuple[int | None, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except Exception:
        return None, b""


def running_server(base: str) -> tuple[str, dict]:
    """État du serveur qui occupe le port : « current », « stale » (ancienne version) ou « foreign »."""
    status, body = fetch(base + "/api/version")
    if status == 200:
        try:
            info = json.loads(body)
        except json.JSONDecodeError:
            info = {}
        if info.get("app") == "DeepLearning":
            same = info.get("code") == config.code_fingerprint() and not info.get("stale")
            return ("current" if same else "stale"), info
    # Première version de DeepLearning (sans /api/version) ?
    status, body = fetch(base + "/")
    if status == 200 and b"DeepLearning" in body:
        return "stale", {}
    return "foreign", {}


def listening_pids(port: int) -> list[int]:
    """PID du ou des processus qui écoutent sur le port (Windows : netstat)."""
    if sys.platform != "win32":
        return []
    try:
        raw = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, timeout=10).stdout or b""
    except Exception:
        return []
    # La console Windows écrit dans la page de code OEM (« État » en français) : décodage tolérant.
    out = raw.decode("oem" if sys.platform == "win32" else "utf-8", errors="replace")
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        # « TCP  127.0.0.1:8765  0.0.0.0:0  LISTENING  1234 » (l'état peut être traduit : on ne s'y fie pas)
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[1].endswith(f":{port}") and parts[2] in ("0.0.0.0:0", "[::]:0"):
            if parts[-1].isdigit():
                pids.add(int(parts[-1]))
    return sorted(pids)


def stop_old_server(info: dict) -> bool:
    pids = {p for p in (info.get("supervisor"), info.get("pid")) if p} or set(listening_pids(config.PORT))
    for pid in sorted(pids):
        if pid == os.getpid():
            continue
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        else:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
    for _ in range(40):
        if not port_in_use(config.HOST, config.PORT):
            return True
        time.sleep(0.25)
    return False


def main() -> None:
    url = f"http://{config.HOST}:{config.PORT}"
    open_browser = "--no-browser" not in sys.argv
    reload = "--no-reload" not in sys.argv

    if port_in_use(config.HOST, config.PORT):
        state, info = running_server(url)
        if state == "current":
            print(f"DeepLearning est déjà lancé (à jour) sur {url}")
            if open_browser:
                webbrowser.open(url)
            return
        if state == "foreign":
            print(f"Le port {config.PORT} est occupé par un autre programme. "
                  "Choisis un autre port : set DEEPLEARNING_PORT=8780 puis relance.")
            return
        print("Une ancienne version de DeepLearning tourne encore : arrêt et redémarrage…")
        if not stop_old_server(info):
            print("Impossible d'arrêter l'ancienne version. Ferme sa fenêtre puis relance lancer.bat.")
            return

    # Les processus du serveur (superviseur + serveur rechargé) connaissent le PID à arrêter plus tard.
    os.environ["DL_SUPERVISOR_PID"] = str(os.getpid())
    if open_browser:
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()
    print(f"DeepLearning est lancé sur {url}  —  Ctrl+C pour arrêter.")
    if reload:
        print("(Rechargement automatique activé : les mises à jour du code sont prises en compte sans relancer.)")
    uvicorn.run(
        "app.main:app",
        host=config.HOST,
        port=config.PORT,
        log_level="warning",
        reload=reload,
        reload_dirs=[str(config.APP_DIR)] if reload else None,
    )


if __name__ == "__main__":
    main()
