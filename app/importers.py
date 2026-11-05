"""Conversion des cours importés (PDF, DOCX, PPTX, anciens DOC / PPT, TXT, MD) en Markdown."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import office

SCAN_MARKER_RE = re.compile(r"^> 📷 \*\*Page (\d+)\*\*.*$", re.MULTILINE)


@dataclass
class ImportResult:
    title: str
    markdown: str
    scanned_pages: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_TD_RE = re.compile(r"(?:^|[^a-z])td(?![a-z])|travaux[\s_-]*dirig", re.IGNORECASE)
_TP_RE = re.compile(r"(?:^|[^a-z])tp(?![a-z])|travaux[\s_-]*pratiq", re.IGNORECASE)
_CR_RE = re.compile(r"compte[\s_-]*rendu|(?:^|[^a-z])(?:cr|rapport)(?![a-z])", re.IGNORECASE)


def guess_kind(filename: str) -> str:
    """Devine le type d'après le nom du fichier : « TD2_routage.pdf » → td, « TP-Docker.pptx » → tp,
    « Compte-rendu TP3.pdf » → rendu, sinon cours (CM)."""
    stem = Path(filename).stem
    if _CR_RE.search(stem):
        return "rendu"
    if _TD_RE.search(stem):
        return "td"
    if _TP_RE.search(stem):
        return "tp"
    return "cours"


def title_from_filename(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[_]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem or "Sans titre"


def import_file(path: Path, original_name: str) -> ImportResult:
    ext = Path(original_name).suffix.lower()
    if ext in office.LEGACY:
        # Ancien .doc / .ppt : Word / PowerPoint le réenregistrent en .docx / .pptx, lu ensuite normalement.
        try:
            modern = office.to_modern(path)
        except office.ConversionError as exc:
            raise ValueError(f"les anciens fichiers {ext} sont convertis avec Microsoft Office — {exc}") from exc
        try:
            return import_file(modern, Path(original_name).stem + modern.suffix)
        finally:
            modern.unlink(missing_ok=True)

    title = title_from_filename(original_name)
    if ext == ".pdf":
        body, scanned, warnings = pdf_to_markdown(path)
    elif ext == ".docx":
        body, warnings = docx_to_markdown(path), []
        scanned = []
    elif ext == ".pptx":
        body, warnings = pptx_to_markdown(path)
        scanned = []
    elif ext in {".txt", ".md", ".markdown"}:
        body, scanned, warnings = read_text(path), [], []
    else:
        raise ValueError(f"Format non pris en charge : {ext}")

    body = cleanup_markdown(body)
    if not body.strip():
        warnings.append("Aucun texte n'a pu être extrait de ce fichier.")
    # Ajoute un titre principal s'il n'y en a pas déjà un.
    if not re.match(r"^#\s", body.lstrip()):
        body = f"# {title}\n\n{body}"
    return ImportResult(title=title, markdown=body.strip() + "\n", scanned_pages=scanned, warnings=warnings)


LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}


def cleanup_markdown(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ").replace("\x00", "")
    for lig, plain in LIGATURES.items():
        text = text.replace(lig, plain)
    text = re.sub(r"[​‌‍⁠﻿]", "", text)  # caractères invisibles (largeur nulle)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _md_escape_cell(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("|", "\\|")).strip()


def rows_to_markdown_table(rows: list[list[str]]) -> str:
    rows = [[_md_escape_cell(c) for c in r] for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


# =========================================================================== mise en forme en ligne

_MONO_FONTS = ("courier", "consolas", "mono", "lucida console", "monaco", "menlo", "source code", "fira code")
_ZERO_WIDTH_RE = re.compile(r"[​‌‍⁠﻿]")


def pieces_to_markdown(pieces: list[list]) -> str:
    """Morceaux [texte, code, gras, italique] → Markdown en ligne (`code`, **gras**, *italique*)."""
    merged: list[list] = []
    for text, *fmt in pieces:
        if merged and merged[-1][1:] == fmt:
            merged[-1][0] += text
        else:
            merged.append([text, *fmt])
    out = []
    for text, code, bold, italic in merged:
        if not code:
            # Un « * » collé à un mot (ex. « wildcard* ») serait pris pour de l'italique : on l'échappe.
            text = re.sub(r"(?<=\S)\*|\*(?=\S)", r"\\*", text)
        core = text.strip()
        if not core or not (code or bold or italic):
            out.append(text)
            continue
        lead, trail = text[: len(text) - len(text.lstrip())], text[len(text.rstrip()) :]
        if code:
            tick = "``" if "`" in core else "`"
            core = f"{tick}{core}{tick}"
        else:
            mark = "***" if (bold and italic) else ("**" if bold else "*")
            core = f"{mark}{core}{mark}"
        out.append(f"{lead}{core}{trail}")
    return re.sub(r"\s+", " ", "".join(out)).strip()


# =========================================================================== PDF

def _normalize_line(text: str) -> str:
    return re.sub(r"\d+", "#", text.strip().lower())


def _is_mono_span(span: dict) -> bool:
    return bool(span.get("flags", 0) & 8) or any(m in span.get("font", "").lower() for m in _MONO_FONTS)


def _is_bold_span(span: dict) -> bool:
    font = span.get("font", "").lower()
    return bool(span.get("flags", 0) & 16) or "bold" in font or "black" in font or "heavy" in font


def _is_italic_span(span: dict) -> bool:
    font = span.get("font", "").lower()
    return bool(span.get("flags", 0) & 2) or "italic" in font or "oblique" in font


def _bullet_marker(spans: list[dict]) -> str | None:
    """Puce en tête de ligne : •, ●, –, puces des polices Symbol/Wingdings (), ou « o » de Word."""
    visible = [s for s in spans if s["text"].strip()]
    if not visible:
        return None
    first = visible[0]["text"].lstrip()
    ch = first[0]
    rest = "".join(s["text"] for s in spans).lstrip()[1:2]
    if 0xF000 <= ord(ch) <= 0xF0FF or ch in "•◦▪▫●○■□►▸‣⁃➢➤✓✔→":
        return ch
    if ch in "-–—*" and (rest[:1].isspace() or len(first.strip()) == 1):
        return ch
    # Sous-puce « o » de Word : un « o » isolé, dans une autre police que le texte qui suit.
    if first.strip() == "o" and len(visible) > 1 and (
        _is_mono_span(visible[0]) or visible[0].get("font") != visible[1].get("font")
    ):
        return "o"
    return None


_HEADING_CONNECTORS = {
    "et", "de", "du", "des", "d’un", "d'un", "d’une", "d'une", "la", "le", "les", "à", "au", "aux", "pour",
    "en", "sur", "avec", "par", "dans", "un", "une", "of", "the", "and", "to", "for", "in", "on", "with", "a", "an",
}


def _heading_continues(prev: dict, line: dict) -> bool:
    """Un titre sur deux lignes ? (« 1. Configuration de base du système et » + « du réseau »)"""
    p, n = prev["text"].strip(), line["text"].strip()
    words = p.rstrip(" :–-").split()
    aligned = abs(prev["x0"] - line["x0"]) < 30
    return bool(
        n[:1].islower()
        or n.startswith("(")
        or p.count("(") > p.count(")")
        or (words and words[-1].lower() in _HEADING_CONNECTORS)
        or p.endswith((":", "–", "-", ","))
        or (aligned and len(n.split()) <= 2)
    )


def _join_text(a: str, b: str) -> str:
    """Recolle deux lignes d'un paragraphe (en supprimant la césure « exclusi- / vement »)."""
    if a.endswith("-") and not a.endswith(" -") and b[:1].islower():
        return a[:-1] + b
    # Mise en forme coupée par le retour à la ligne : « **intelligence** » + « **artificielle** ».
    for mark in ("**", "`"):
        if a.endswith(mark) and b.startswith(mark) and not a.endswith(mark * 2 if mark == "`" else "***"):
            return f"{a[: -len(mark)]} {b[len(mark):]}"
    return f"{a} {b}" if a else b


def pdf_to_markdown(path: Path) -> tuple[str, list[int], list[str]]:
    """PDF → Markdown, ligne par ligne : titres (taille / gras), paragraphes (espacement vertical),
    puces, blocs de code (police à chasse fixe), tableaux, et repères pour les images."""
    import pymupdf

    warnings: list[str] = []
    doc = pymupdf.open(path)
    if doc.needs_pass:
        raise ValueError("Ce PDF est protégé par un mot de passe.")

    flags = (
        pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES & ~pymupdf.TEXT_PRESERVE_LIGATURES
    ) | pymupdf.TEXT_DEHYPHENATE
    size_chars: Counter[float] = Counter()
    pages: list[dict] = []
    bullet_x: list[float] = []

    for page in doc:
        page_area = page.rect.width * page.rect.height or 1
        info = {"items": [], "chars": 0, "has_images": False, "height": page.rect.height or 842}
        # Tableaux : extraits à part pour garder leur structure.
        table_boxes = []
        try:
            for t in page.find_tables().tables:
                md = t.to_markdown(clean=False).strip()
                if md and len(t.extract()) >= 2:
                    table_boxes.append(pymupdf.Rect(t.bbox))
                    info["items"].append({"y0": t.bbox[1], "x0": t.bbox[0], "table": md})
        except Exception:  # find_tables peut échouer sur certains PDF exotiques
            pass
        # Images assez grandes (captures d'écran, schémas) : on laisse un repère dans le texte.
        try:
            for img in page.get_image_info():
                box = pymupdf.Rect(img["bbox"])
                info["has_images"] = True
                if box.get_area() >= 0.04 * page_area and not any(box.intersects(tb) for tb in table_boxes):
                    info["items"].append({"y0": box.y0, "x0": box.x0, "image": True})
        except Exception:
            info["has_images"] = len(page.get_images(full=False)) > 0

        lines: list[dict] = []
        data = page.get_text("dict", flags=flags, sort=True)
        for b_index, block in enumerate(data.get("blocks", [])):
            if block.get("type") != 0:
                continue
            for raw in block.get("lines", []):
                spans = []
                for s in raw.get("spans", []):
                    text = _ZERO_WIDTH_RE.sub("", s.get("text", "")).replace("\xa0", " ")
                    if text:
                        spans.append({**s, "text": text})
                x0, y0, x1, y1 = raw["bbox"]
                center = pymupdf.Point((x0 + x1) / 2, (y0 + y1) / 2)
                if any(center in tb for tb in table_boxes):
                    continue
                text = "".join(s["text"] for s in spans)
                visible = [s for s in spans if s["text"].strip()]
                line = {
                    "spans": spans, "text": text, "block": b_index, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                    "size": max((round(s["size"], 1) for s in visible), default=0),
                    "bold": bool(visible) and all(_is_bold_span(s) for s in visible),
                    "mono": bool(visible) and all(_is_mono_span(s) for s in visible),
                    "blank": not visible, "edge": False,
                }
                prev = lines[-1] if lines else None
                # Morceaux d'une même ligne visuelle (« 6 » + « Critères », puce + texte) : on recolle.
                if (
                    prev is not None and not prev["blank"] and not line["blank"]
                    and abs(y0 - prev["y0"]) < 2.5 and -2 <= x0 - prev["x1"] < 40
                ):
                    prev["spans"] += [{"text": " ", "flags": 0, "font": "", "size": 0}] + spans
                    prev["text"] += " " + text
                    prev["x1"], prev["y1"] = x1, max(prev["y1"], y1)
                    prev["size"] = max(prev["size"], line["size"])
                    prev["bold"] = prev["bold"] and line["bold"]
                    prev["mono"] = prev["mono"] and line["mono"]
                    continue
                lines.append(line)

        content = [ln for ln in lines if not ln["blank"]]
        for ln in content:
            for s in ln["spans"]:
                if s["text"].strip() and not _is_mono_span(s):
                    size_chars[round(s["size"], 1)] += len(s["text"])
            info["chars"] += len(ln["text"].strip())
            ln["marker"] = _bullet_marker(ln["spans"])
            if ln["marker"]:
                bullet_x.append(ln["x0"])
        # Lignes candidates aux en-têtes / pieds de page : 2 premières, 2 dernières, ou dans les marges.
        h = info["height"]
        for idx, ln in enumerate(content):
            if idx < 2 or idx >= len(content) - 2 or ln["y0"] < 0.08 * h or ln["y0"] > 0.92 * h:
                ln["edge"] = len(ln["text"]) < 120
        info["items"] += lines
        info["items"].sort(key=lambda it: (round(it["y0"]), it["x0"]))
        pages.append(info)

    n_pages = len(pages)
    body_size = size_chars.most_common(1)[0][0] if size_chars else 11.0
    min_bullet_x = min(bullet_x) if bullet_x else 0

    repeated: set[str] = set()
    if n_pages >= 4:
        counter: Counter[str] = Counter()
        for p in pages:
            counter.update({_normalize_line(it["text"]) for it in p["items"] if it.get("edge")})
        repeated = {k for k, v in counter.items() if v >= max(3, 0.6 * n_pages)}

    out: list[str] = []
    scanned: list[int] = []
    cur: dict | None = None  # paragraphe / puce / bloc de code en cours (peut continuer sur la page suivante)

    def flush() -> None:
        nonlocal cur
        if cur:
            if cur["kind"] == "code":
                body = "\n".join(cur["lines"]).replace("```", "ʼʼʼ")
                if out and out[-1].startswith("```\n") and out[-1].endswith("\n```"):
                    # Blocs de code consécutifs (fichier de config aéré, suite de commandes) : on les réunit.
                    out[-1] = f"{out[-1][:-4]}\n\n{body}\n```"
                else:
                    out.append(f"```\n{body}\n```")
            elif cur["kind"] == "li":
                if cur["text"].strip():
                    out.append(f"{'  ' * cur['level']}- {cur['text']}")
            elif cur["text"].strip():
                out.append(cur["text"])
        cur = None

    for page_no, p in enumerate(pages, start=1):
        if p["chars"] < 25 and p["has_images"]:
            flush()
            scanned.append(page_no)
            out.append(f"> 📷 **Page {page_no}** : page scannée (image), texte non extrait.")
            continue

        prev_line: dict | None = None
        last_heading: dict | None = None
        # Une phrase coupée par le changement de page continue sur la page suivante.
        carry = cur is not None and cur["kind"] in ("p", "li") and not cur["text"].rstrip().endswith(
            (".", ":", "!", "?", ";", "»")
        )
        pending_break = not carry

        for it in p["items"]:
            if "table" in it or "image" in it:
                flush()
                out.append(
                    it["table"] if "table" in it
                    else f"> 🖼️ *Image (capture d'écran, schéma…) — voir l'original, page {page_no}*"
                )
                prev_line, last_heading, pending_break = None, None, True
                continue
            ln = it
            if ln["blank"]:
                pending_break = True
                continue
            if ln["edge"] and _normalize_line(ln["text"]) in repeated:
                continue

            size = ln["size"] or body_size
            gap = ln["y0"] - prev_line["y1"] if prev_line else 999
            had_blank = pending_break
            if prev_line is None and carry and not had_blank:
                brk = False  # suite du paragraphe de la page précédente
            else:
                brk = (
                    had_blank or prev_line is None or gap > 0.9 * size
                    or (ln["block"] != prev_line["block"] and gap > 0.4 * size)
                )
            carry = False
            pending_break = False
            text = re.sub(r"\s+", " ", ln["text"]).strip()
            ratio = size / body_size if body_size else 1
            short = len(text) < 120 and not text.endswith((".", ",", ";"))

            level = 0
            if not ln["marker"] and not ln["mono"] and short:
                if ratio >= 1.6:
                    level = 2
                elif ratio >= 1.15:
                    level = 3
                elif ln["bold"] and len(text) < 80 and brk:
                    level = 4
            if level:
                flush()
                if (
                    last_heading and last_heading["level"] == level and last_heading["index"] == len(out) - 1
                    and ln["y0"] - last_heading["line"]["y1"] < 0.6 * size and _heading_continues(last_heading["line"], ln)
                ):
                    out[-1] += " " + text
                    last_heading["line"] = {**ln, "text": out[-1].lstrip("# "), "x0": last_heading["line"]["x0"]}
                else:
                    out.append(f"{'#' * level} {text}")
                    last_heading = {"level": level, "index": len(out) - 1, "line": ln}
                prev_line = ln
                continue
            last_heading = None

            if ln["mono"] and not ln["marker"]:
                continues = cur is not None and cur["kind"] == "code" and not had_blank and gap < 0.9 * size
                if not continues:
                    flush()
                    cur = {"kind": "code", "lines": [], "x0": ln["x0"]}
                indent = max(0, round((ln["x0"] - cur["x0"]) / (0.6 * size))) if size else 0
                cur["lines"].append(" " * indent + ln["text"].rstrip())
                prev_line = ln
                continue

            spans = ln["spans"]
            if ln["marker"]:
                # On retire la puce du premier morceau de texte visible.
                spans = [dict(s) for s in spans]
                for s in spans:
                    if s["text"].strip():
                        s["text"] = s["text"].lstrip()[1:]
                        break
            rich = pieces_to_markdown(
                [[s["text"], _is_mono_span(s), _is_bold_span(s), _is_italic_span(s)] for s in spans]
            )
            if ln["marker"]:
                flush()
                offset = ln["x0"] - min_bullet_x
                cur = {"kind": "li", "text": rich, "level": 0 if offset <= 10 else (1 if offset <= 45 else 2)}
            elif cur and cur["kind"] in ("p", "li") and not brk:
                cur["text"] = _join_text(cur["text"], rich)
            else:
                flush()
                cur = {"kind": "p", "text": rich}
            prev_line = ln
        if cur and cur["kind"] == "code":
            flush()
    flush()

    # Titre réduit à un numéro (« 1 ») suivi du vrai titre : on les fusionne.
    merged: list[str] = []
    for block in out:
        m = re.fullmatch(r"(#{2,4}) (\d{1,2})\.?", merged[-1]) if merged else None
        h = re.match(r"(#{2,4}) (.+)", block)
        if m and h:
            merged[-1] = f"{h.group(1)} {m.group(2)}. {h.group(2)}"
        else:
            merged.append(block)

    md = "\n\n".join(merged)
    # Les éléments de liste consécutifs ne doivent pas être séparés par des lignes vides.
    md = re.sub(r"(?m)^(\s*- .*)\n\n(?=\s*- )", r"\1\n", md)
    md = re.sub(r"(?m)^(\s*- .*)\n\n(?=\s*- )", r"\1\n", md)
    if scanned:
        warnings.append(
            f"{len(scanned)} page(s) semblent scannées (sans texte). "
            "Utilise « Transcrire les pages scannées » pour les lire avec l'IA."
        )
    doc.close()
    return md, scanned, warnings


def render_pdf_page_png(path: Path, page_number: int, dpi: int = 150) -> bytes:
    import pymupdf

    with pymupdf.open(path) as doc:
        page = doc[page_number - 1]
        pix = page.get_pixmap(dpi=dpi)
        return pix.tobytes("png")


# =========================================================================== DOCX

def _runs_to_markdown(paragraph) -> str:
    from docx.text.hyperlink import Hyperlink
    from docx.text.run import Run

    pieces: list[tuple[str, bool, bool]] = []
    try:
        content = list(paragraph.iter_inner_content())
    except AttributeError:
        content = list(paragraph.runs)
    for item in content:
        if isinstance(item, Hyperlink):
            text = item.text
            url = getattr(item, "address", "") or ""
            if text.strip() and url:
                pieces.append((f"[{text.strip()}]({url})", False, False))
            elif text:
                pieces.append((text, False, False))
        elif isinstance(item, Run):
            pieces.append((item.text, bool(item.bold), bool(item.italic)))

    # Regroupe les runs consécutifs ayant la même mise en forme.
    merged: list[list] = []
    for text, b, i in pieces:
        if merged and merged[-1][1] == b and merged[-1][2] == i:
            merged[-1][0] += text
        else:
            merged.append([text, b, i])

    result = []
    for text, b, i in merged:
        if not text.strip() or not (b or i):
            result.append(text)
            continue
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()) :]
        core = text.strip()
        mark = "***" if (b and i) else ("**" if b else "*")
        result.append(f"{lead}{mark}{core}{mark}{trail}")
    return "".join(result).strip()


def _docx_table(table) -> str:
    rows = []
    for row in table.rows:
        cells = []
        prev = None
        for cell in row.cells:
            if prev is not None and cell._tc is prev:  # cellule fusionnée horizontalement
                continue
            prev = cell._tc
            cells.append(cell.text)
        rows.append(cells)
    return rows_to_markdown_table(rows)


def docx_to_markdown(path: Path) -> str:
    from docx import Document
    from docx.table import Table

    document = Document(str(path))
    out: list[str] = []
    try:
        items = list(document.iter_inner_content())
    except AttributeError:
        items = list(document.paragraphs)

    for item in items:
        if isinstance(item, Table):
            md = _docx_table(item)
            if md:
                out.append(md)
            continue
        paragraph = item
        text = _runs_to_markdown(paragraph)
        if not text:
            continue
        style = ((paragraph.style.name if paragraph.style is not None else "") or "").lower()
        heading = re.match(r"(heading|titre)\s*(\d)", style)
        p_pr = paragraph._p.pPr
        num_pr = p_pr.numPr if p_pr is not None else None
        plain = re.sub(r"\*+", "", text)

        if heading:
            level = min(int(heading.group(2)) + 1, 6)
            out.append(f"{'#' * level} {plain}")
        elif style in {"title", "titre"}:
            out.append(f"# {plain}")
        elif style in {"subtitle", "sous-titre"}:
            out.append(f"*{plain}*")
        elif num_pr is not None or "list" in style or "liste" in style:
            level = 0
            if num_pr is not None and num_pr.ilvl is not None and num_pr.ilvl.val is not None:
                level = int(num_pr.ilvl.val)
            marker = "1." if ("number" in style or "numéro" in style) else "-"
            out.append(f"{'  ' * level}{marker} {text}")
        elif "quote" in style or "citation" in style:
            out.append(f"> {text}")
        else:
            out.append(text)

    md = "\n\n".join(out)
    md = re.sub(r"(?m)^(\s*(?:-|1\.) .*)\n\n(?=\s*(?:-|1\.) )", r"\1\n", md)
    md = re.sub(r"(?m)^(\s*(?:-|1\.) .*)\n\n(?=\s*(?:-|1\.) )", r"\1\n", md)
    return md


# =========================================================================== PPTX

def _iter_shapes(shapes):
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_shapes(shape.shapes)
        else:
            yield shape


_PROMPT_RE = re.compile(r"^\s*\S+@[\w.-]+:\S*\s?[$#]\s")


def _para_text(para) -> str:
    """Texte d'un paragraphe en gardant les retours à la ligne forcés (Maj+Entrée)."""
    return (para.text or "").replace("\x0b", "\n").replace("\xa0", " ")


def _rich_para_text(para) -> str:
    """Texte d'un paragraphe en Markdown : police à chasse fixe → `code`, gras, italique."""
    from pptx.text.text import _Run

    pieces: list[list] = []  # [texte, code, gras, italique]
    for child in para._p:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "r":
            run = _Run(child, para)
            name = (run.font.name or "").lower()
            fmt = [any(m in name for m in _MONO_FONTS), bool(run.font.bold), bool(run.font.italic)]
            text = run.text.replace("\xa0", " ").replace("\x0b", " ")
        elif tag == "br":
            fmt, text = [False, False, False], " "
        elif tag == "fld":
            fmt, text = [False, False, False], "".join(child.itertext())
        else:
            continue
        pieces.append([text, *fmt])
    return pieces_to_markdown(pieces)


def _find_title(slide):
    """Le titre de la diapo. python-pptx ne le trouve pas toujours (exports Google Slides)."""
    from pptx.enum.shapes import PP_PLACEHOLDER

    title = slide.shapes.title
    if title is not None:
        return title
    for shape in slide.placeholders:
        try:
            if shape.placeholder_format.type in (
                PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE, PP_PLACEHOLDER.VERTICAL_TITLE
            ):
                return shape
        except ValueError:
            continue
    return None


def _is_code_shape(shape) -> bool:
    """Zone de texte en police à chasse fixe ou ressemblant à un terminal (« user@host:~$ ls »)."""
    if not shape.has_text_frame:
        return False
    texts = [_para_text(p) for p in shape.text_frame.paragraphs]
    if not any(t.strip() for t in texts):
        return False
    if any(_PROMPT_RE.match(line) for t in texts for line in t.split("\n")):
        return True
    fonts = [
        (r.font.name or "").lower()
        for p in shape.text_frame.paragraphs
        for r in p.runs
        if r.text.strip() and r.font.name
    ]
    return bool(fonts) and all(any(m in f for m in _MONO_FONTS) for f in fonts)


def _code_block(shape) -> str:
    lines: list[str] = []
    for p in shape.text_frame.paragraphs:
        lines.extend(_para_text(p).split("\n"))
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    body = "\n".join(line.rstrip() for line in lines).replace("```", "ʼʼʼ")
    return f"```\n{body}\n```"


def _contains_center(host, shape) -> bool:
    if None in (host.top, host.left, host.height, host.width, shape.top, shape.left, shape.height, shape.width):
        return False
    cy, cx = shape.top + shape.height / 2, shape.left + shape.width / 2
    return host.top <= cy <= host.top + host.height and host.left <= cx <= host.left + host.width


def _text_shape_lines(shape, codes: list) -> list[str]:
    """Puces d'une zone de texte, avec les blocs de code posés par-dessus insérés au bon endroit.

    Les diapos placent souvent un encadré « terminal » sur des lignes laissées vides du corps du texte :
    on estime la position verticale de chaque paragraphe pour y glisser le bloc de code correspondant."""
    import math

    paras = list(shape.text_frame.paragraphs)
    inserts: dict[int, list] = {}
    if codes and paras:
        weights = [max(1, math.ceil(len(_para_text(p)) / 70)) for p in paras]
        total = sum(weights)
        starts, acc = [], 0
        for w in weights:
            starts.append(acc / total)
            acc += w
        empties = [i for i, p in enumerate(paras) if not _para_text(p).strip()]
        for code in sorted(codes, key=lambda s: s.top or 0):
            frac = (code.top + code.height / 2 - shape.top) / (shape.height or 1)
            if empties:
                i = min(empties, key=lambda k: abs(starts[k] + weights[k] / (2 * total) - frac))
            else:
                i = max([k for k in range(len(paras)) if starts[k] <= frac] or [len(paras) - 1])
            inserts.setdefault(i, []).append(code)

    lines: list[str] = []
    for i, para in enumerate(paras):
        try:
            text = _rich_para_text(para)
        except Exception:  # structure XML inattendue : on se contente du texte brut
            text = re.sub(r"\s+", " ", _para_text(para)).strip()
        if text:
            lines.append(f"{'  ' * (para.level or 0)}- {text}")
        for code in inserts.get(i, []):
            lines += ["", _code_block(code), ""]
    return lines


def pptx_to_markdown(path: Path) -> tuple[str, list[str]]:
    from pptx import Presentation

    prs = Presentation(str(path))
    out: list[str] = []
    empty_slides = 0
    for index, slide in enumerate(prs.slides, start=1):
        title_shape = _find_title(slide)
        title = ""
        if title_shape is not None and title_shape.has_text_frame:
            parts = [re.sub(r"\s+", " ", _para_text(p)).strip() for p in title_shape.text_frame.paragraphs]
            title = " – ".join(p for p in parts if p)
        out.append(f"## {index}. {title}" if title else f"## Diapositive {index}")

        shapes = [
            s for s in _iter_shapes(slide.shapes) if title_shape is None or s.shape_id != title_shape.shape_id
        ]
        shapes.sort(key=lambda s: ((s.top or 0), (s.left or 0)))
        codes = [s for s in shapes if _is_code_shape(s)]
        hosts = [s for s in shapes if s.has_text_frame and s not in codes]
        placed: dict[int, list] = {}
        standalone = set()
        for code in codes:
            host = next((h for h in hosts if _contains_center(h, code)), None)
            if host is not None:
                placed.setdefault(host.shape_id, []).append(code)
            else:
                standalone.add(code.shape_id)

        slide_lines: list[str] = []
        for shape in shapes:
            if shape in codes:
                if shape.shape_id in standalone:
                    slide_lines += ["", _code_block(shape), ""]
            elif shape.has_text_frame:
                slide_lines += _text_shape_lines(shape, placed.get(shape.shape_id, []))
            elif getattr(shape, "has_table", False) and shape.has_table:
                rows = [[cell.text for cell in row.cells] for row in shape.table.rows]
                md = rows_to_markdown_table(rows)
                if md:
                    slide_lines.append("\n" + md + "\n")
            elif getattr(shape, "has_chart", False) and shape.has_chart:
                chart = shape.chart
                if chart.has_title and chart.chart_title.has_text_frame:
                    slide_lines.append(f"- *(graphique : {chart.chart_title.text_frame.text.strip()})*")
        if any(line.strip() for line in slide_lines):
            out.append("\n".join(slide_lines).strip("\n"))
        elif not title:
            empty_slides += 1

        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip() if slide.notes_slide.notes_text_frame else ""
            if notes:
                quoted = "\n".join(f"> {line}" if line.strip() else ">" for line in notes.splitlines())
                out.append(f"> **Notes de l'orateur :**\n{quoted}")

    warnings = []
    if empty_slides:
        warnings.append(f"{empty_slides} diapositive(s) ne contiennent que des images (texte non extrait).")
    return "\n\n".join(out), warnings
