"""Lance DeepLearning et ouvre l'interface dans le navigateur.

Usage :  python run.py [--no-browser]
"""

from __future__ import annotations

import socket
import sys
import threading
import webbrowser

import uvicorn

from app import config


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def main() -> None:
    url = f"http://{config.HOST}:{config.PORT}"
    open_browser = "--no-browser" not in sys.argv
    if port_in_use(config.HOST, config.PORT):
        print(f"DeepLearning semble déjà lancé sur {url}")
        if open_browser:
            webbrowser.open(url)
        return
    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"DeepLearning est lancé sur {url}  —  Ctrl+C pour arrêter.")
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
