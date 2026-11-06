"""Rendu Markdown → HTML assaini (aperçu de l'éditeur, export)."""

from __future__ import annotations

import html
import re

import markdown
import nh3

EXTENSIONS = [
    "extra",
    "sane_lists",
    "toc",
    "pymdownx.arithmatex",
    "pymdownx.tasklist",
    "pymdownx.mark",
    "pymdownx.tilde",
    "pymdownx.betterem",
]
EXTENSION_CONFIGS = {
    "pymdownx.arithmatex": {"generic": True},
    "pymdownx.tasklist": {"custom_checkbox": False},
}

_TAGS = set(nh3.ALLOWED_TAGS) | {"input"}
_CELL_ATTRS = {"style", "align", "colspan", "rowspan"}
_ATTRIBUTES = {
    "*": {"class", "id", "title"},
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "input": {"type", "checked", "disabled"},
    "th": _CELL_ATTRS,
    "td": _CELL_ATTRS,
    "ol": {"start"},
    "details": {"open"},
}


_ONE_LINE_DISPLAY = re.compile(r"\$\$(.+?)\$\$")


def normalize_math(text: str) -> str:
    """Les modèles écrivent souvent « $$…$$ » collé au texte : on isole ces formules en blocs
    (lignes vides autour) pour qu'elles soient reconnues, et on convertit en formule en ligne celles
    qui sont au milieu d'une phrase."""
    out: list[str] = []
    in_code = in_math = False
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith(("```", "~~~")) and not in_math:
            in_code = not in_code
            out.append(line)
            continue
        if in_code:
            out.append(line)
            continue
        if in_math:
            out.append(line)
            if s.endswith("$$"):
                in_math = False
                out.append("")
            continue
        m = _ONE_LINE_DISPLAY.fullmatch(s)
        if m:
            if out and out[-1].strip():
                out.append("")
            out += ["$$", m.group(1).strip(), "$$", ""]
        elif s == "$$" or (s.startswith("$$") and "$$" not in s[2:]):
            if out and out[-1].strip():
                out.append("")
            out.append(line)
            in_math = True
        else:
            # Une liste collée à la ligne de texte précédente n'est pas reconnue par Python-Markdown :
            # on insère la ligne vide qu'il attend (sauf à l'intérieur d'une liste existante).
            if _LIST_ITEM.match(line) and out:
                prev = out[-1]
                if prev.strip() and not _LIST_ITEM.match(prev) and not prev[:1].isspace():
                    out.append("")
            out.append(_ONE_LINE_DISPLAY.sub(r"$\1$", line))
    return "\n".join(out)


_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+\S")


def to_html(text: str) -> str:
    md = markdown.Markdown(extensions=EXTENSIONS, extension_configs=EXTENSION_CONFIGS)
    raw = md.convert(normalize_math(text or ""))
    return nh3.clean(
        raw,
        tags=_TAGS,
        attributes=_ATTRIBUTES,
        filter_style_properties={"text-align"},
        url_schemes={"http", "https", "mailto"},
    )


EXPORT_TEMPLATE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"
  onload="renderMathInElement(document.body,{{delimiters:[{{left:'\\\\[',right:'\\\\]',display:true}},{{left:'\\\\(',right:'\\\\)',display:false}}]}})"></script>
<style>
  body {{ font: 16px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; color: #1f2937; max-width: 820px;
         margin: 40px auto; padding: 0 24px; }}
  h1, h2, h3, h4 {{ line-height: 1.25; color: #111827; }}
  h1 {{ border-bottom: 3px solid #6366f1; padding-bottom: .3em; }}
  h2 {{ border-bottom: 1px solid #e5e7eb; padding-bottom: .2em; margin-top: 1.6em; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
  th, td {{ border: 1px solid #d1d5db; padding: 6px 10px; text-align: left; vertical-align: top; }}
  th {{ background: #f3f4f6; }}
  code {{ background: #f3f4f6; padding: .1em .35em; border-radius: 4px; font-size: .92em; }}
  pre {{ background: #f3f4f6; padding: 12px; border-radius: 8px; overflow-x: auto; }}
  pre code {{ background: none; padding: 0; }}
  blockquote {{ margin: 1em 0; padding: .5em 1em; border-left: 4px solid #6366f1; background: #eef2ff; }}
  mark {{ background: #fef08a; }}
  img {{ max-width: 100%; height: auto; border-radius: 6px; }}
  @media print {{ img {{ break-inside: avoid; }} }}
  @media print {{ body {{ margin: 0; max-width: none; }} h2 {{ break-after: avoid; }} }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def export_html(title: str, text: str) -> str:
    return EXPORT_TEMPLATE.format(title=html.escape(title), body=to_html(text))
