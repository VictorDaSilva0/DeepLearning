"""Conversion DOCX / PPTX → PDF via Microsoft Office (Windows) pour afficher l'original dans l'outil.

On pilote Word / PowerPoint par COM depuis un petit script PowerShell : aucune dépendance Python
supplémentaire, et un processus séparé qu'on peut interrompre si Office se bloque.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

_lock = threading.Lock()  # Office n'aime pas les conversions simultanées.

_WORD = r"""
$ErrorActionPreference = 'Stop'
$word = New-Object -ComObject Word.Application
try {
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $doc = $word.Documents.Open($env:DL_IN, $false, $true, $false)
    try { $doc.ExportAsFixedFormat($env:DL_OUT, 17) } finally { $doc.Close(0) }
} finally {
    if ($word.Documents.Count -eq 0) { $word.Quit() }
}
"""

# PowerPoint est mono-instance : on ne le ferme que si aucune autre présentation n'est ouverte.
_POWERPOINT = r"""
$ErrorActionPreference = 'Stop'
$ppt = New-Object -ComObject PowerPoint.Application
try {
    $pres = $ppt.Presentations.Open($env:DL_IN, -1, 0, 0)
    try { $pres.SaveAs($env:DL_OUT, 32) } finally { $pres.Close() }
} finally {
    if ($ppt.Presentations.Count -eq 0) { $ppt.Quit() }
}
"""


class ConversionError(Exception):
    pass


def available() -> bool:
    return sys.platform == "win32"


def preview_path(source: Path) -> Path:
    return source.with_name(source.stem + ".preview.pdf")


def to_pdf(source: Path, timeout: int = 180) -> Path:
    """Convertit (une seule fois, avec cache) un .docx/.pptx en PDF. Renvoie le chemin du PDF."""
    target = preview_path(source)
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return target
    if not available():
        raise ConversionError("L'aperçu des fichiers Word / PowerPoint nécessite Microsoft Office sous Windows.")
    ext = source.suffix.lower()
    script = {".docx": _WORD, ".pptx": _POWERPOINT}.get(ext)
    if not script:
        raise ConversionError(f"Aperçu non pris en charge pour {ext}.")

    tmp = target.with_name(target.stem + ".tmp.pdf")
    env = dict(os.environ, DL_IN=str(source.resolve()), DL_OUT=str(tmp.resolve()))
    with _lock:
        if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
            return target
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError as exc:
            raise ConversionError("PowerShell est introuvable.") from exc
        except subprocess.TimeoutExpired as exc:
            raise ConversionError("Office met trop de temps à convertir le fichier.") from exc
        if proc.returncode != 0 or not tmp.exists():
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            hint = detail[0][:300] if detail else "erreur inconnue"
            tmp.unlink(missing_ok=True)
            raise ConversionError(f"Conversion impossible (Microsoft Office est-il installé ?) : {hint}")
        tmp.replace(target)
    return target
