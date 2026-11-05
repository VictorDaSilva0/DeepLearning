"""Conversions via Microsoft Office (Windows) :

- Word / PowerPoint → PDF, pour afficher le cours d'origine dans l'outil ;
- anciens formats .doc / .ppt → .docx / .pptx, pour pouvoir les importer.

On pilote Word / PowerPoint par COM depuis un petit script PowerShell : aucune dépendance Python
supplémentaire, et un processus séparé qu'on peut interrompre si Office se bloque. Les macros des
fichiers ouverts sont désactivées.

Un Word non activé (« Produit sans licence ») ouvre et lit les documents mais se bloque dès qu'on lui
demande d'enregistrer ou d'exporter. Pour les .doc on se contente donc de lire leur contenu (format
Open XML « à plat », que Word fournit même sans licence) et on reconstruit le .docx nous-mêmes.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
import threading
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import quoteattr

_lock = threading.Lock()  # Office n'aime pas les conversions simultanées.

_UNLICENSED = "DL_UNLICENSED"

# Ouverture commune : invisible, sans alertes, macros désactivées. Mot de passe factice : un document
# protégé provoque une erreur au lieu d'une fenêtre de saisie invisible qui bloquerait tout.
_WORD_HEAD = r"""
$ErrorActionPreference = 'Stop'
$word = New-Object -ComObject Word.Application
try {
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
"""
_WORD_OPEN = "$doc = $word.Documents.Open($env:DL_IN, $false, $true, $false, 'dl-no-password')\n"
_WORD_TAIL = r"""
} finally {
    if ($word.Documents.Count -eq 0) { $word.Quit() }
}
"""

# Export PDF. Sans licence, le titre de Word devient « Word (Produit sans licence) » : on s'arrête tout de suite.
_WORD_PDF = (
    _WORD_HEAD
    + f"    if ($word.Caption -match '\\(') {{ throw '{_UNLICENSED}' }}\n    "
    + _WORD_OPEN
    + "    try { $doc.ExportAsFixedFormat($env:DL_OUT, 17) } finally { $doc.Close(0) }"
    + _WORD_TAIL
)

# Lecture du contenu au format Open XML « à plat » (aucun enregistrement : fonctionne même sans licence).
_WORD_XML = (
    _WORD_HEAD
    + "    "
    + _WORD_OPEN
    + "    try { [IO.File]::WriteAllText($env:DL_OUT, $doc.Content.WordOpenXML, (New-Object Text.UTF8Encoding $false)) }"
    + " finally { $doc.Close(0) }"
    + _WORD_TAIL
)

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

_PDF_SCRIPTS = {".docx": _WORD_PDF, ".doc": _WORD_PDF, ".pptx": _POWERPOINT, ".ppt": _POWERPOINT}
LEGACY = {".doc": ".docx", ".ppt": ".pptx"}  # anciens formats importables → format moderne


class ConversionError(Exception):
    pass


def available() -> bool:
    return sys.platform == "win32"


def preview_path(source: Path) -> Path:
    return source.with_name(source.stem + ".preview.pdf")


def _kill_stuck_office(since: datetime) -> None:
    """Arrête les instances Word / PowerPoint lancées par COM (« /Automation ») pendant une conversion
    bloquée — jamais un Word / PowerPoint ouvert par l'utilisateur."""
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='WINWORD.EXE' or Name='POWERPNT.EXE'\" | "
        "Where-Object { $_.CommandLine -match '/automation' -and $_.CreationDate -ge [datetime]$env:DL_SINCE } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    )
    env = dict(os.environ, DL_SINCE=since.strftime("%Y-%m-%dT%H:%M:%S"))
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            env=env, capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


def _run(script: str, source: Path, out: Path, timeout: int, fmt: int = 0) -> None:
    env = dict(os.environ, DL_IN=str(source.resolve()), DL_OUT=str(out.resolve()), DL_FMT=str(fmt))
    started = datetime.now() - timedelta(seconds=2)
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            env=env,
            capture_output=True,
            text=True,
            encoding="oem",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as exc:
        raise ConversionError("PowerShell est introuvable.") from exc
    except subprocess.TimeoutExpired as exc:
        _kill_stuck_office(started)
        out.unlink(missing_ok=True)
        raise ConversionError("Office ne répond pas (conversion interrompue).") from exc
    if proc.returncode != 0 or not out.exists():
        out.unlink(missing_ok=True)
        detail = (proc.stderr or proc.stdout or "").strip()
        if _UNLICENSED in detail:
            raise ConversionError(
                "Word n'est pas activé sur ce PC (« Produit sans licence ») : sans activation, "
                "il refuse de convertir le document en PDF pour l'aperçu"
            )
        low = detail.lower()
        if "password" in low or "mot de passe" in low:
            raise ConversionError("Le fichier est protégé par un mot de passe.")
        lines = detail.splitlines()
        hint = lines[0][:300] if lines else "erreur inconnue"
        raise ConversionError(f"Conversion impossible (Microsoft Office est-il installé ?) : {hint}")


def _check(source: Path, allowed: set[str] | dict) -> str:
    ext = source.suffix.lower()
    if not available():
        raise ConversionError("Cette conversion nécessite Microsoft Office sous Windows.")
    if ext not in allowed:
        raise ConversionError(f"Conversion non prise en charge pour {ext}.")
    return ext


def to_pdf(source: Path, timeout: int = 180) -> Path:
    """Convertit (une seule fois, avec cache) un fichier Word / PowerPoint en PDF. Renvoie le chemin du PDF."""
    target = preview_path(source)
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return target
    ext = _check(source, _PDF_SCRIPTS)
    tmp = target.with_name(target.stem + ".tmp.pdf")
    with _lock:
        if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
            return target
        _run(_PDF_SCRIPTS[ext], source, tmp, timeout, fmt=32)
        tmp.replace(target)
    return target


def to_modern(source: Path, timeout: int = 120) -> Path:
    """Convertit un ancien .doc / .ppt en .docx / .pptx (fichier créé à côté, à supprimer après usage)."""
    ext = _check(source, LEGACY)
    target = source.with_name(source.stem + ".converted" + LEGACY[ext])
    with _lock:
        if ext == ".ppt":
            _run(_POWERPOINT, source, target, timeout, fmt=24)
            return target
        flat = source.with_name(source.stem + ".flat.xml")
        try:
            _run(_WORD_XML, source, flat, timeout)
            flat_opc_to_package(flat, target)
        finally:
            flat.unlink(missing_ok=True)
    return target


_PKG = "{http://schemas.microsoft.com/office/2006/xmlPackage}"


def flat_opc_to_package(flat: Path, target: Path) -> None:
    """Open XML « à plat » (un seul fichier XML, tel que Word le fournit) → paquet .docx (zip)."""
    from lxml import etree

    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    root = etree.parse(str(flat), parser).getroot()
    types: list[tuple[str, str]] = []
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for part in root.iter(_PKG + "part"):
            name = part.get(_PKG + "name") or ""
            if not name.startswith("/"):
                continue
            xml_data = part.find(_PKG + "xmlData")
            if xml_data is not None:
                child = next(iter(xml_data), None)
                if child is None:
                    continue
                data = etree.tostring(child, xml_declaration=True, encoding="UTF-8", standalone=True)
            else:
                binary = part.find(_PKG + "binaryData")
                data = base64.b64decode("".join((binary.text or "").split())) if binary is not None else b""
            zf.writestr(name.lstrip("/"), data)
            types.append((name, part.get(_PKG + "contentType") or "application/octet-stream"))
        overrides = "".join(f"<Override PartName={quoteattr(n)} ContentType={quoteattr(c)}/>" for n, c in types)
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f"{overrides}</Types>",
        )
    if not any(n == "/word/document.xml" for n, _ in types):
        target.unlink(missing_ok=True)
        raise ConversionError("Word n'a renvoyé aucun contenu pour ce document.")
