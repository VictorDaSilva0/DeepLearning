"""Conversion des cours importés (PDF, DOCX, PPTX, TXT, MD) en Markdown."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

BULLET_CHARS = "•◦▪▫●○■□►▸‣⁃–—-*✓✔➢➤→"
SCAN_MARKER_RE = re.compile(r"^> 📷 \*\*Page (\d+)\*\*.*$", re.MULTILINE)


@dataclass
class ImportResult:
    title: str
    markdown: str
    scanned_pages: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_TD_RE = re.compile(r"(?:^|[^a-z])td(?![a-z])|travaux[\s_-]*dirig", re.IGNORECASE)
_TP_RE = re.compile(r"(?:^|[^a-z])tp(?![a-z])|travaux[\s_-]*pratiq", re.IGNORECASE)


def guess_kind(filename: str) -> str:
    """Devine le type d'après le nom du fichier : « TD2_routage.pdf » → td, « TP-Docker.pptx » → tp, sinon cours (CM)."""
    stem = Path(filename).stem
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


# =========================================================================== PDF

def _normalize_line(text: str) -> str:
    return re.sub(r"\d+", "#", text.strip().lower())


def _is_bullet(text: str) -> bool:
    return len(text) > 1 and text[0] in BULLET_CHARS and (text[1].isspace() or text[0] in "•◦▪●○■►▸➢➤✓✔")


def _strip_bullet(text: str) -> str:
    return text[1:].strip()


def pdf_to_markdown(path: Path) -> tuple[str, list[int], list[str]]:
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

    for page in doc:
        page_info = {
            "items": [],
            "chars": 0,
            "images": len(page.get_images(full=False)) > 0,
            "height": page.rect.height or 842,
        }
        # Tableaux : on les extrait à part pour garder leur structure.
        table_boxes = []
        try:
            tables = page.find_tables()
            for t in tables.tables:
                md = t.to_markdown(clean=False).strip()
                if md and len(t.extract()) >= 2:
                    table_boxes.append(pymupdf.Rect(t.bbox))
                    page_info["items"].append({"y": t.bbox[1], "table": md})
        except Exception:  # find_tables peut échouer sur certains PDF exotiques
            pass

        data = page.get_text("dict", flags=flags, sort=True)
        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue
            bbox = pymupdf.Rect(block["bbox"])
            if any(bbox.intersects(tb) and (bbox & tb).get_area() > 0.5 * bbox.get_area() for tb in table_boxes):
                continue
            lines = []
            for line in block.get("lines", []):
                spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
                if not spans:
                    continue
                text = "".join(s["text"] for s in line["spans"]).strip()
                size = max(round(s["size"], 1) for s in spans)
                bold = all((s.get("flags", 0) & 16) or "bold" in s.get("font", "").lower() for s in spans)
                for s in spans:
                    size_chars[round(s["size"], 1)] += len(s["text"])
                page_info["chars"] += len(text)
                lines.append({"text": text, "size": size, "bold": bold, "y": line["bbox"][1], "edge": False})
            if lines:
                page_info["items"].append({"y": block["bbox"][1], "lines": lines})
        page_info["items"].sort(key=lambda it: it["y"])
        # Lignes candidates aux en-têtes / pieds de page : 2 premières, 2 dernières, ou en marge.
        all_lines = [ln for it in page_info["items"] for ln in it.get("lines", [])]
        h = page_info["height"]
        for idx, ln in enumerate(all_lines):
            if idx < 2 or idx >= len(all_lines) - 2 or ln["y"] < 0.08 * h or ln["y"] > 0.92 * h:
                ln["edge"] = len(ln["text"]) < 120
        pages.append(page_info)

    n_pages = len(pages)
    body_size = size_chars.most_common(1)[0][0] if size_chars else 11.0

    # Lignes répétées en haut / bas de la plupart des pages (en-têtes, pieds de page, numéros).
    repeated: set[str] = set()
    if n_pages >= 4:
        counter: Counter[str] = Counter()
        for p in pages:
            counter.update({_normalize_line(ln["text"]) for it in p["items"] for ln in it.get("lines", []) if ln["edge"]})
        repeated = {k for k, v in counter.items() if v >= max(3, 0.6 * n_pages)}

    out: list[str] = []
    scanned: list[int] = []
    for page_no, p in enumerate(pages, start=1):
        if p["chars"] < 25 and p["images"]:
            scanned.append(page_no)
            out.append(f"> 📷 **Page {page_no}** : page scannée (image), texte non extrait.")
            continue
        for it in p["items"]:
            if "table" in it:
                out.append(it["table"])
                continue
            para: list[str] = []
            kind = "p"

            def flush() -> None:
                nonlocal para, kind
                if para:
                    text = " ".join(para).strip()
                    text = re.sub(r"\s+", " ", text)
                    out.append(f"- {text}" if kind == "li" else text)
                para, kind = [], "p"

            for ln in it["lines"]:
                text = ln["text"]
                if ln["edge"] and _normalize_line(text) in repeated:
                    continue
                ratio = ln["size"] / body_size if body_size else 1
                short = len(text) < 120 and not text.endswith((".", ",", ";"))
                if ratio >= 1.6 and short:
                    flush()
                    out.append(f"## {text}")
                elif ratio >= 1.2 and short:
                    flush()
                    out.append(f"### {text}")
                elif ln["bold"] and short and len(text) < 80 and len(it["lines"]) <= 2 and not _is_bullet(text):
                    flush()
                    out.append(f"#### {text}")
                elif _is_bullet(text):
                    flush()
                    kind = "li"
                    para = [_strip_bullet(text)]
                else:
                    para.append(text)
            flush()

    md = "\n\n".join(out)
    # Les éléments de liste consécutifs ne doivent pas être séparés par des lignes vides.
    md = re.sub(r"(?m)^(- .*)\n\n(?=- )", r"\1\n", md)
    md = re.sub(r"(?m)^(- .*)\n\n(?=- )", r"\1\n", md)
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


_MONO_FONTS = ("courier", "consolas", "mono", "lucida console", "monaco", "menlo", "source code", "fira code")
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
        if pieces and pieces[-1][1:] == fmt:
            pieces[-1][0] += text
        else:
            pieces.append([text, *fmt])

    out = []
    for text, code, bold, italic in pieces:
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
