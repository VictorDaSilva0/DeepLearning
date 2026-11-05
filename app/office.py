"""Conversions via Microsoft Office (Windows) :

- Word / PowerPoint → PDF, pour afficher le cours d'origine dans l'outil ;
- anciens formats .doc / .ppt → .docx / .pptx, pour pouvoir les importer.

On pilote Word / PowerPoint par COM depuis un petit script PowerShell : aucune dépendance Python
supplémentaire, et un processus séparé qu'on peut interrompre si Office se bloque. Les macros des
fichiers ouverts sont désactivées.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

_lock = threading.Lock()  # Office n'aime pas les conversions simultanées.

# DL_FMT : 17 = PDF (wdExportFormatPDF), sinon un format de SaveAs2 (16 = .docx).
# Mot de passe factice : un document protégé provoque une erreur au lieu d'une fenêtre invisible bloquante.
_WORD = r"""
$ErrorActionPreference = 'Stop'
$word = New-Object -ComObject Word.Application
try {
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    $doc = $word.Documents.Open($env:DL_IN, $false, $true, $false, 'dl-no-password')
    try {
        if ($env:DL_FMT -eq '17') { $doc.ExportAsFixedFormat($env:DL_OUT, 17) }
        else { $doc.SaveAs2($env:DL_OUT, [int]$env:DL_FMT) }
    } finally { $doc.Close(0) }
} finally {
    if ($word.Documents.Count -eq 0) { $word.Quit() }
}
"""

# PowerPoint est mono-instance : on ne le ferme que si aucune autre présentation n'est ouverte,
# et on rétablit son réglage de macros (il peut s'agir de la fenêtre PowerPoint de l'utilisateur).
# DL_FMT : 32 = PDF, 24 = .pptx.
_POWERPOINT = r"""
$ErrorActionPreference = 'Stop'
$ppt = New-Object -ComObject PowerPoint.Application
$security = $ppt.AutomationSecurity
try {
    $ppt.AutomationSecurity = 3
    $pres = $ppt.Presentations.Open($env:DL_IN, -1, 0, 0)
    try { $pres.SaveAs($env:DL_OUT, [int]$env:DL_FMT) } finally { $pres.Close() }
} finally {
    $ppt.AutomationSecurity = $security
    if ($ppt.Presentations.Count -eq 0) { $ppt.Quit() }
}
"""

# extension → (script, code du format PDF)
_APPS = {
    ".docx": (_WORD, 17),
    ".doc": (_WORD, 17),
    ".pptx": (_POWERPOINT, 32),
    ".ppt": (_POWERPOINT, 32),
}
# Anciens formats → (extension moderne, code du format)
LEGACY = {".doc": (".docx", 16), ".ppt": (".pptx", 24)}


class ConversionError(Exception):
    pass


def available() -> bool:
    return sys.platform == "win32"


def preview_path(source: Path) -> Path:
    return source.with_name(source.stem + ".preview.pdf")


def _run(source: Path, out: Path, fmt: int, timeout: int) -> None:
    script = _APPS[source.suffix.lower()][0]
    env = dict(os.environ, DL_IN=str(source.resolve()), DL_OUT=str(out.resolve()), DL_FMT=str(fmt))
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
        out.unlink(missing_ok=True)
        raise ConversionError("Office met trop de temps à convertir le fichier.") from exc
    if proc.returncode != 0 or not out.exists():
        out.unlink(missing_ok=True)
        detail = (proc.stderr or proc.stdout or "").strip()
        if "password" in detail.lower() or "mot de passe" in detail.lower():
            raise ConversionError("le fichier est protégé par un mot de passe.")
        lines = detail.splitlines()
        hint = lines[0][:300] if lines else "erreur inconnue"
        raise ConversionError(f"Conversion impossible (Microsoft Office est-il installé ?) : {hint}")


def _check(source: Path) -> None:
    if not available():
        raise ConversionError("Cette conversion nécessite Microsoft Office sous Windows.")
    if source.suffix.lower() not in _APPS:
        raise ConversionError(f"Conversion non prise en charge pour {source.suffix.lower()}.")


def to_pdf(source: Path, timeout: int = 180) -> Path:
    """Convertit (une seule fois, avec cache) un fichier Word / PowerPoint en PDF. Renvoie le chemin du PDF."""
    target = preview_path(source)
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return target
    _check(source)
    tmp = target.with_name(target.stem + ".tmp.pdf")
    with _lock:
        if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
            return target
        _run(source, tmp, _APPS[source.suffix.lower()][1], timeout)
        tmp.replace(target)
    return target


def to_modern(source: Path, timeout: int = 180) -> Path:
    """Convertit un ancien .doc / .ppt en .docx / .pptx (fichier créé à côté, à supprimer après usage)."""
    _check(source)
    ext = source.suffix.lower()
    if ext not in LEGACY:
        raise ConversionError(f"{ext} n'est pas un ancien format Office.")
    new_ext, fmt = LEGACY[ext]
    target = source.with_name(source.stem + ".converted" + new_ext)
    with _lock:
        _run(source, target, fmt, timeout)
    return target
