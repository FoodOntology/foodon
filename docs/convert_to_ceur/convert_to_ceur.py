#!/usr/bin/env python3
"""
Google Doc → CEURART PDF converter.
Fetches content from a public Google Doc and produces a CEURART-formatted PDF.
All paper metadata (authors, conference, abstract, etc.) is persisted in
paper_config.json inside a working folder (default: ./temp/).

Usage:
    python3 convert_to_ceur.py -i <google-doc-url>  # first-time setup for a new paper
    python3 convert_to_ceur.py                       # re-run with saved settings in ./temp/
    python3 convert_to_ceur.py -f myfolder           # use a different working folder
    python3 convert_to_ceur.py --engine tex          # force LaTeX engine (tectonic or pdflatex)
    python3 convert_to_ceur.py --engine lo           # force LibreOffice engine
    python3 convert_to_ceur.py --keep-tex            # save intermediate paper.tex in folder
    python3 convert_to_ceur.py --settings other.json # override settings file path
    python3 convert_to_ceur.py -e                    # re-extract metadata from current doc

LaTeX engine (better CEURART fidelity): brew install tectonic
LibreOffice engine (fallback, already installed): uses CEUR-Template-1col.odt for styles
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import argparse
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip3 install requests")

SCRIPT_DIR   = Path(__file__).parent.resolve()
CEURART_CLS  = SCRIPT_DIR / "ceurart.cls"
CEURART_BST  = SCRIPT_DIR / "elsarticle-num-names.bst"
REF_ODT      = SCRIPT_DIR / "CEUR-Template-1col.odt"
LIBREOFFICE  = "/Applications/LibreOffice.app/Contents/MacOS/soffice"

DEFAULT_SETTINGS = {
    "_comment": "Edit this file to configure your paper. Run convert_to_ceur.py to regenerate the PDF. File: paper_config.json",
    "doc_url": "",
    "export_format": "docx",
    "output_pdf": "paper.pdf",
    "pdf_engine": "auto",
    "title": "",
    "title_note": "",
    "conference": "FILL IN: Workshop Name, Month DD-DD, YYYY, City, Country",
    "copyrightyear": "2026",
    "copyrightclause": "Copyright for this paper by its authors. Use permitted under Creative Commons License Attribution 4.0 International (CC BY 4.0).",
    "abstract": "",
    "abstract_from_doc": True,
    "abstract_heading": "Abstract",
    "remove_abstract_from_body": True,
    "keywords": [],
    "keywords_heading": "Keywords",
    "remove_keywords_from_body": True,
    "authors": [],
    "affiliations": [],
    "cortext": "Corresponding author.",
    "acknowledgments": "",
    "genai_uses": [],
    "genai_tools": "",
    "genai_declaration": "",
    "bib_file": "",
    "pandoc_extra_args": [],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def latex_escape(text: str) -> str:
    """Escape special LaTeX characters in plain-text metadata strings."""
    replacements = [
        ('\\', r'\textbackslash{}'), ('&', r'\&'), ('%', r'\%'),
        ('$', r'\$'), ('#', r'\#'), ('_', r'\_'), ('{', r'\{'),
        ('}', r'\}'), ('~', r'\textasciitilde{}'), ('^', r'\textasciicircum{}'),
    ]
    for ch, esc in replacements:
        text = text.replace(ch, esc)
    return text


def extract_doc_id(url: str) -> str:
    m = re.search(r'/document/d/([a-zA-Z0-9_-]+)', url)
    if not m:
        sys.exit(f"Could not parse Google Doc ID from URL: {url}")
    return m.group(1)


def download_doc(url: str, fmt: str, dest: Path) -> Path:
    doc_id = extract_doc_id(url)
    export_url = f"https://docs.google.com/document/d/{doc_id}/export?format={fmt}"
    print(f"  Fetching Google Doc as {fmt} ...")
    resp = requests.get(export_url, allow_redirects=True, timeout=60)
    if resp.status_code in (401, 403) or 'accounts.google.com' in resp.url:
        sys.exit(
            "Error: document requires authentication.\n"
            "Set sharing to 'Anyone with the link can view' in Google Docs."
        )
    resp.raise_for_status()
    outfile = dest / f"document.{fmt}"
    outfile.write_bytes(resp.content)
    print(f"  Downloaded {len(resp.content):,} bytes")
    return outfile


def run_pandoc(args: list, input_text: str | None = None) -> str:
    result = subprocess.run(
        ['pandoc'] + args, input=input_text, capture_output=True, text=True
    )
    if result.returncode != 0 and result.stderr:
        print(f"  pandoc warning: {result.stderr[:300]}", file=sys.stderr)
    return result.stdout


def clean_markdown(text: str) -> str:
    """Strip all inline font styling except bold and italic.

    Pandoc represents Google Docs formatting as span attributes, e.g.:
      [text]{.underline}   [text]{.mark}   [text]{.smallcaps}
      [text]{color="red"}  ~~strikethrough~~

    These all need to go — layout is controlled by the CEURART template.
    Bold (**text**) and italic (*text*) are preserved.
    Superscripts (^x^) and subscripts (~x~) are preserved for citations
    and scientific notation.

    Nested spans are handled by looping until the output stabilises.
    Links ([text](url)) are unaffected because their delimiter is ( not {.
    """
    # Strip span attributes: [content]{...} → content
    # Use [^\[\]] so nested spans are peeled from the inside out each pass.
    span_pat = re.compile(r'\[([^\[\]]*)\]\{[^}]+\}')
    prev = None
    while prev != text:
        prev = text
        text = span_pat.sub(r'\1', text)

    # Strip strikethrough: ~~text~~ → text
    text = re.sub(r'~~(.+?)~~', r'\1', text, flags=re.DOTALL)

    return text


# ---------------------------------------------------------------------------
# OBO PURL -> CURIE  (e.g. http://purl.obolibrary.org/obo/FOODON_00004348 -> FOODON:00004348)
# ---------------------------------------------------------------------------

_OBO_PURL_RE = re.compile(
    r'^https?://purl\.obolibrary\.org/obo/([A-Za-z]+)_([A-Za-z0-9]+)$'
)

# Pandoc renders a hyperlink whose visible text equals its URL as an autolink
# <url>; any other visible text produces an explicit link [text](url).
_AUTOLINK_RE = re.compile(
    r'<(https?://purl\.obolibrary\.org/obo/[A-Za-z]+_[A-Za-z0-9]+)>'
)
_MDLINK_RE = re.compile(
    r'\[([^\[\]]*)\]\((https?://purl\.obolibrary\.org/obo/[A-Za-z]+_[A-Za-z0-9]+)\)'
)


def curie_for_url(url: str) -> str | None:
    """Return a CURIE (e.g. FOODON:00004348) for a recognized OBO PURL, else None."""
    m = _OBO_PURL_RE.match(url.strip())
    return f'{m.group(1)}:{m.group(2)}' if m else None


def _looks_like_same_id(text: str, curie: str) -> bool:
    """True if text is just a different spelling of the same id (e.g. FOODON_00004348)."""
    prefix, local_id = curie.split(':', 1)
    normalized = re.sub(r'[_:\s]', '', text).upper()
    return normalized == (prefix + local_id).upper()


def linkify_curies(markdown: str) -> str:
    """Replace hyperlinks to OBO PURLs with the same link shown as a CURIE.

    Works anywhere in the markdown, including table cells (pipe/grid tables
    are just inline markdown content), e.g.:
      <http://purl.obolibrary.org/obo/FOODON_00004348>
        -> [FOODON:00004348](http://purl.obolibrary.org/obo/FOODON_00004348)
      [FOODON_00004722](http://purl.obolibrary.org/obo/FOODON_00004722)
        -> [FOODON:00004722](http://purl.obolibrary.org/obo/FOODON_00004722)

    Explicit links are only rewritten when their visible text is already just
    the id (under_score or colon form) — links with descriptive text (e.g.
    "[the raw food term](...)") are left untouched so prose isn't clobbered.
    """
    def repl_autolink(m):
        url = m.group(1)
        curie = curie_for_url(url)
        return f'[{curie}]({url})' if curie else m.group(0)

    def repl_mdlink(m):
        text, url = m.group(1), m.group(2)
        curie = curie_for_url(url)
        if curie and _looks_like_same_id(text, curie):
            return f'[{curie}]({url})'
        return m.group(0)

    markdown = _AUTOLINK_RE.sub(repl_autolink, markdown)
    markdown = _MDLINK_RE.sub(repl_mdlink, markdown)
    return markdown


# ---------------------------------------------------------------------------
# Adjacent table merging
# ---------------------------------------------------------------------------
# Word/Google Docs sometimes splits one logical table into several table
# objects (e.g. when the author copy-pasted in sections, or it got split
# across a page break without "repeat header row" carrying real header text
# into the new table object). Pandoc always treats a table's first row as
# its header, so the second fragment's first row — actually a normal data
# row — gets silently turned into a bold heading and visually detached from
# its table.
#
# Policy: a table is only "new" if a "Table N: ..." caption sits directly
# above it. Any table that follows another with nothing (no caption, no
# prose) in between is assumed to be a continuation of the same table, and
# is merged into it, recovering its first row as ordinary data.

_LONGTABLE_DETAIL_RE = re.compile(
    r'\\begin\{longtable\}\[\]\{@\{\}\n'
    r'(?P<colspec>.*?)'
    r'@\{\}\}\n'
    r'\\toprule\\noalign\{\}\n'
    r'(?P<header>.*?)\n'
    r'\\midrule\\noalign\{\}\n'
    r'\\endhead\n'
    r'\\bottomrule\\noalign\{\}\n'
    r'\\endlastfoot\n'
    r'(?P<body>.*?)\n'
    r'\\end\{longtable\}',
    re.DOTALL
)
_MINIPAGE_CELL_RE = re.compile(
    # An empty cell is rendered as "\raggedright\n\end{minipage}" — only one
    # newline, not two — so the trailing newline must be part of the lazy
    # capture (stripped off after matching) rather than a separate required token.
    r'\\begin\{minipage\}\[b\]\{\\linewidth\}\\raggedright\n(.*?)\\end\{minipage\}',
    re.DOTALL
)
def _header_row_as_data(header: str) -> str:
    cells = [c.rstrip('\n') for c in _MINIPAGE_CELL_RE.findall(header)]
    return ' & '.join(cells) + r' \\'


def merge_adjacent_tables(latex_body: str) -> str:
    """Merge consecutive longtables with nothing between them into one."""
    while True:
        tables = list(_LONGTABLE_DETAIL_RE.finditer(latex_body))
        for first, second in zip(tables, tables[1:]):
            gap = latex_body[first.end():second.start()]
            if gap.strip():
                continue  # caption or prose sits between them — leave separate

            recovered_row = _header_row_as_data(second.group('header'))
            new_body = first.group('body') + '\n' + recovered_row
            if second.group('body').strip():
                new_body += '\n' + second.group('body')

            merged_table = (
                '\\begin{longtable}[]{@{}\n' + first.group('colspec') + '@{}}\n'
                r'\toprule\noalign{}' + '\n' + first.group('header') + '\n'
                r'\midrule\noalign{}' + '\n'
                r'\endhead' + '\n'
                r'\bottomrule\noalign{}' + '\n'
                r'\endlastfoot' + '\n'
                + new_body + '\n'
                r'\end{longtable}'
            )
            latex_body = latex_body[:first.start()] + merged_table + latex_body[second.end():]
            break
        else:
            return latex_body


# ---------------------------------------------------------------------------
# Table/figure caption styling
# ---------------------------------------------------------------------------
# Caption paragraphs come in two flavors depending on how the author typed
# them in the Google Doc: a single typed line ("Table 1: some text"), or
# Google Docs' native auto-numbered caption field, which pandoc renders as
# two separate paragraphs ("Table 1" alone, then "some text" alone, no
# colon). Either form may sit directly above or directly below its
# table/figure. ceurart's own \caption styling (bold sans-serif label, no
# paragraph indent, table captions above the table) is only wired up inside
# its custom table/figure float environments, which pandoc's longtable/
# includegraphics output never enters — so we detect these caption
# paragraphs after the markdown to LaTeX conversion and re-style/reposition
# them to match.

_SPLIT_CAPTION_RE = re.compile(
    r'^(Table|Figure)\s+([^\s:\n]+)\n\n(?!(?:Table|Figure)\s)([^\n]+)$',
    re.MULTILINE
)
_LONGTABLE_RE = re.compile(r'\\begin\{longtable\}[\s\S]*?\\end\{longtable\}')
_TABLE_CAPTION_LINE_RE = re.compile(r'Table\s+([^\s:]+):[ \t]*([^\n]+)')
_FIGURE_CAPTION_RE = re.compile(
    r'^Figure\s+([^\s:]+):\s*(.+)$', re.MULTILINE
)


def style_captions(latex_body: str) -> str:
    """Style 'Table N: ...' / 'Figure N: ...' caption paragraphs to match
    ceurart's caption look, moving table captions above their table when
    they aren't already (figure captions are left wherever they are,
    typically already below their figure). Relies on
    \\doctablecaption/\\docfigcaption being defined in the document
    preamble (see build_tex).
    """
    # Normalize Google Docs' native auto-numbered caption field, which
    # pandoc splits into two bare paragraphs, into a single "Label: text" line.
    latex_body = _SPLIT_CAPTION_RE.sub(
        lambda m: f'{m.group(1)} {m.group(2)}: {m.group(3)}', latex_body
    )

    pieces = []
    pos = 0
    for m in _LONGTABLE_RE.finditer(latex_body):
        gap = latex_body[pos:m.start()]
        table_block = m.group(0)

        # Caption directly above the table already?
        gap_stripped = gap.rstrip('\n')
        split_idx = gap_stripped.rfind('\n\n')
        last_para = gap_stripped[split_idx + 2:] if split_idx != -1 else gap_stripped
        before = _TABLE_CAPTION_LINE_RE.fullmatch(last_para)
        if before:
            prefix = gap_stripped[:split_idx + 2] if split_idx != -1 else ''
            pieces.append(prefix)
            pieces.append(f'\\doctablecaption{{Table {before.group(1)}}}{{{before.group(2)}}}\n\n')
            pieces.append(table_block)
            pos = m.end()
            continue

        # Caption directly below the table — move it above.
        tail = latex_body[m.end():]
        tail_trimmed = tail.lstrip('\n')
        leading_ws = len(tail) - len(tail_trimmed)
        split_idx2 = tail_trimmed.find('\n\n')
        first_para = tail_trimmed[:split_idx2] if split_idx2 != -1 else tail_trimmed
        after = _TABLE_CAPTION_LINE_RE.fullmatch(first_para)
        if after:
            consumed = leading_ws + len(first_para) + (2 if split_idx2 != -1 else 0)
            pieces.append(gap)
            pieces.append(f'\\doctablecaption{{Table {after.group(1)}}}{{{after.group(2)}}}\n\n')
            pieces.append(table_block)
            pos = m.end() + consumed
            continue

        pieces.append(gap)
        pieces.append(table_block)
        pos = m.end()
    pieces.append(latex_body[pos:])
    latex_body = ''.join(pieces)

    def repl_figure(m):
        label, text = m.group(1), m.group(2)
        return f'\\docfigcaption{{Figure {label}}}{{{text}}}'

    return _FIGURE_CAPTION_RE.sub(repl_figure, latex_body)


_SUPER_TRANS = str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹', '0123456789')


def split_preamble(markdown: str) -> tuple[str, str]:
    """Split off everything before the first section heading.
    Returns (preamble, rest_of_document).
    """
    m = re.search(r'^#{1,3}\s+\S', markdown, re.MULTILINE)
    if m and m.start() > 0:
        return markdown[:m.start()].strip(), markdown[m.start():]
    return '', markdown


def _parse_author_line(text: str) -> list[dict]:
    """Parse 'Name^affils^, Name^affils^, ...' into author dicts.

    Superscript format from pandoc: ^1^, ^1,2^, ^1\\*^ (escaped *).
    The \\* or * inside superscript marks the corresponding author.
    """
    parts = re.split(r',\s*(?=[A-Z])', text.strip())
    authors = []
    for part in parts:
        part = part.strip().rstrip(',')
        m = re.match(r'^(.+?)\s*\^([^^]+)\^\s*$', part)
        if m:
            name = m.group(1).strip()
            sup  = m.group(2)
        else:
            name = part
            sup  = ''
        corresponding = '*' in sup
        affil_nums    = re.findall(r'\d+', sup)
        authors.append({
            'name':               name,
            'affils':             ','.join(affil_nums) if affil_nums else '1',
            'orcid':              '',
            'email':              '',
            'url':                '',
            'corresponding':      corresponding,
            'equal_contribution': False,
        })
    return [a for a in authors if a['name']]


def extract_doc_metadata(preamble: str) -> dict:
    """Parse authors, affiliations, ORCIDs, keywords, title from front-matter markdown.

    Expected Google Doc preamble conventions (after pandoc DOCX→markdown):
      Title paragraph (first non-Authors line)
      Authors: Name^1\\*^, Name^2^, ...
      ^1^ Institution, City, Country
      \\* Corresponding author email@example.com
      **ORCIDS:**
      XX: [https://orcid.org/XXXX-XXXX-XXXX-XXXX](...)
      **Keywords:** word, word, ...

    Unicode superscript digits (¹²³...) are also accepted.
    """
    text = preamble.translate(_SUPER_TRANS)
    result = {}

    # Title: first non-blank paragraph that isn't the Authors line
    for para in re.split(r'\n{2,}', text):
        para = para.strip()
        if para and not re.match(r'authors?\s*[:\-]', para, re.IGNORECASE):
            title = re.sub(r'\^[^^]*\^|[*_`]', '', para).strip()
            if title:
                result['title'] = title
                break

    # Authors line
    m = re.search(r'^Authors?\s*[:\-]\s*(.+)$', text, re.MULTILINE | re.IGNORECASE)
    if m:
        result['authors'] = _parse_author_line(m.group(1))

    # Corresponding author email — handle plain text and mailto: hyperlinks
    # Plain:  \* Corresponding author email@example.com
    # Link:   \* Corresponding author [email](mailto:email@example.com)
    m = re.search(
        r'[Cc]orresponding\s+author.*?(?:\(mailto:([^)]+)\)|(\b[\w.+\-]+@[\w.\-]+\b))',
        text
    )
    if m:
        result['corresponding_email'] = (m.group(1) or m.group(2)).strip()

    # Affiliations: "^N^ Institution text"
    # Skip entries whose text starts with another ^N^ (empty affiliation slot
    # that pandoc merged onto the same line as the next numbered entry).
    affils = {}
    for m in re.finditer(r'^\^(\d+)\^[\s:]+(.+)$', text, re.MULTILINE):
        val = m.group(2).strip()
        if not re.match(r'^\^\d+\^', val):
            affils[m.group(1)] = val
    if affils:
        result['affiliations'] = [
            {'num': k, 'text': v}
            for k, v in sorted(affils.items(), key=lambda x: int(x[0]))
        ]

    # ORCIDs — pandoc renders hyperlinks as [text](url); after clean_markdown
    # the span on the text is stripped, leaving [url](url) or <url>.
    orcids = {}
    for m in re.finditer(
        r'^(\w{1,4})\s*[:\-]\s*(?:\[[^\]]*\]\s*)?\(?(https?://orcid\.org/([^)\s>]+))\)?',
        text, re.MULTILINE
    ):
        initials = m.group(1).upper()
        orcids[initials] = m.group(3)
    if orcids:
        result['orcids_by_initials'] = orcids

    # Keywords (inline: **Keywords:** word, word)
    # The bold markers around "Keywords:" can leave trailing "**" before the list.
    m = re.search(
        r'\*{1,2}Keywords?\s*\*{0,2}\s*:\s*\*{0,2}\s*(.+?)$',
        text, re.MULTILINE | re.IGNORECASE
    )
    if m:
        kws_raw = re.sub(r'^\*+\s*', '', m.group(1))  # strip any stray bold markers
        kws = [k.strip() for k in re.split(r'[,;]', kws_raw) if k.strip()]
        if kws:
            result['keywords'] = kws

    return result


def _is_placeholder(value) -> bool:
    """Return True if value is empty, blank, or a FILL IN placeholder."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip().startswith('FILL IN')
    if isinstance(value, list):
        return len(value) == 0 or all(_is_placeholder(v) for v in value)
    if isinstance(value, dict):
        return all(_is_placeholder(v) for v in value.values())
    return False


def apply_doc_metadata(settings: dict, meta: dict,
                        settings_path: Path, force: bool = False) -> None:
    """Merge auto-detected metadata into settings; save JSON if anything changed.

    Fields are overwritten when empty/placeholder-valued OR when force=True (--extract).
    """
    if not meta:
        return
    changed = []

    def maybe_set(key, value, label):
        if value and (_is_placeholder(settings.get(key)) or force):
            settings[key] = value
            changed.append(label)

    maybe_set('title', meta.get('title'),
              f"title: {meta.get('title', '')[:55]}")
    maybe_set('keywords', meta.get('keywords'),
              f"keywords ({len(meta.get('keywords', []))})")

    # Authors — assign ORCIDs by matching initials, then attach corresponding email
    if meta.get('authors') and (_is_placeholder(settings.get('authors')) or force):
        authors = [dict(a) for a in meta['authors']]
        orcids  = meta.get('orcids_by_initials', {})
        email   = meta.get('corresponding_email', '')
        for author in authors:
            words = author['name'].split()
            f, l = words[0], words[-1]
            for initials in filter(None, [
                ''.join(w[0] for w in words).upper(),          # all-word: DD, HM, MAJ
                (f[0] + l[0]).upper() if len(words) >= 2 else '',        # first+last: DD
                (f[0] + l[:2]).upper() if len(words) >= 2 else '',       # F+La: DMC
                (f[:2] + l[0]).upper() if len(f) >= 2 else '',           # Fi+L: DAD
            ]):
                if initials in orcids:
                    author['orcid'] = orcids[initials]
                    break
            if author.get('corresponding') and email:
                author['email'] = email
        settings['authors'] = authors
        n_orcid = sum(1 for a in authors if a['orcid'])
        changed.append(f"authors ({len(authors)}, {n_orcid} with ORCID)")

    maybe_set('affiliations', meta.get('affiliations'),
              f"affiliations ({len(meta.get('affiliations', []))})")

    if changed:
        settings_path.write_text(json.dumps(settings, indent=2))
        print(f"  Extracted → {', '.join(changed)}")
        print(f"  Saved to {settings_path.name}")


def extract_section(markdown: str, heading: str) -> tuple[str, str]:
    """
    Find a section with the given heading in markdown, return
    (section_body_as_markdown, markdown_with_section_removed).
    Matches headings at any level (#, ##, ###) or bold-only paragraphs.
    """
    # Try ATX headings first
    pat = re.compile(
        r'^(#{1,4})\s+' + re.escape(heading) + r'\s*$',
        re.MULTILINE | re.IGNORECASE
    )
    m = pat.search(markdown)
    if m:
        level = len(m.group(1))
        start = m.end()
        next_h = re.compile(r'^#{1,' + str(level) + r'}\s+\S', re.MULTILINE)
        n = next_h.search(markdown, start)
        end = n.start() if n else len(markdown)
        content = markdown[start:end].strip()
        rest = (markdown[:m.start()] + markdown[end:]).strip()
        return content, rest

    # Try bold paragraph (**Abstract** or __Abstract__)
    pat2 = re.compile(
        r'^[\*_]{2}' + re.escape(heading) + r'[\*_]{2}\s*$',
        re.MULTILINE | re.IGNORECASE
    )
    m2 = pat2.search(markdown)
    if m2:
        start = m2.end()
        # Content until next blank line + non-blank line that starts new section
        next_section = re.compile(r'\n\n(?=[#\*_\-\d])', re.MULTILINE)
        n2 = next_section.search(markdown, start)
        end = n2.start() if n2 else len(markdown)
        content = markdown[start:end].strip()
        rest = (markdown[:m2.start()] + markdown[end:]).strip()
        return content, rest

    return '', markdown


# ---------------------------------------------------------------------------
# GenAI declaration  (https://ceur-ws.org/GenAI/Policy.html)
# ---------------------------------------------------------------------------

# Each entry: (key, label, acceptable use, unacceptable use)
# Acceptable + Unacceptable from: https://ceur-ws.org/GenAI/Policy.html
# Taxonomy descriptions (Acceptable column only): https://ceur-ws.org/GenAI/Taxonomy.html
# Order follows the taxonomy page.
GENAI_USES = [
    ('text_creation', 'Drafting Content',
     # Taxonomy description + Policy – Acceptable
     "AI can help you write different sections of your paper, such as introductions, "
     "literature reviews, or methodology descriptions. "
     "While GenAI can assist with writer's block or retrieving definitions, its use "
     "should be contingent upon human critical thinking and judgment to ensure "
     "accuracy and originality.",
     # Policy – Unacceptable
     "Using GenAI to generate new text, such as paragraphs, or even entire sections "
     "of a paper is ethically unacceptable. "
     "Academic writing must be original and attributed to human authors."),
    ('image_creation', 'Generate Images',
     # Taxonomy description + Policy – Acceptable
     "AI can help you generate images for your paper. "
     "This is acceptable only when the paper's core topic is about automatic "
     "image generation.",
     # Policy – Unacceptable
     "Employing GenAI to create visual aids, such as diagrams, charts, and "
     "illustrations is unacceptable."),
    ('translation', 'Text Translation',
     # Taxonomy description + Policy – Acceptable
     "AI can help you in translating your work or reaching a broader audience. "
     "This involves using GenAI to translate text from another language into "
     "English or vice versa.",
     # Policy – Unacceptable
     "Employing GenAI to translate a previously published work into English, "
     "without subsequent editorial refinement, raises ethical concerns about "
     "self-plagiarism."),
    ('literature_review', 'Generate Literature Review',
     # Taxonomy description
     "AI can help you drafting a literature review section starting from a set "
     "of relevant papers.",
     ''),
    ('rephrasing', 'Paraphrase and Reword',
     # Taxonomy description + Policy – Acceptable
     "AI can help you express ideas in different ways, ensuring clarity and "
     "conciseness. "
     "GenAI can help you rephrase sentences or paragraphs to improve clarity, "
     "conciseness, or style.",
     # Policy – Unacceptable
     "GenAI's ability to process large textual data allows it to seemingly process "
     "minor sentences to whole paragraphs. Refining whole paragraphs without human "
     "critical thinking and judgment can perpetuate biases from its training data "
     "and erode the author's narrative voice."),
    ('improve_style', 'Improve Writing Style',
     # Taxonomy description
     "AI can offer suggestions for sentence structure, word choice, and overall flow.",
     ''),
    ('abstract_drafting', 'Abstract Drafting',
     # Taxonomy description
     "AI can draft a concise abstract that captures the gist of your research.",
     ''),
    ('grammar', 'Grammar and Spelling Check',
     # Taxonomy description + Policy – Acceptable
     "AI can catch errors that you might have missed. "
     "GenAI can be used to identify and correct grammatical errors, typos, and "
     "other writing mistakes.",
     # Policy – Unacceptable
     "Refining whole paragraphs without human critical thinking and judgment can "
     "perpetuate biases from its training data and erode the author's narrative "
     "voice."),
    ('plagiarism_detection', 'Plagiarism Detection',
     # Taxonomy description
     "AI can help you identify potential plagiarism issues in your own writing.",
     ''),
    ('citation_management', 'Citation Management',
     # Taxonomy description
     "AI can help format citations and references according to specific styles "
     "(e.g., APA, MLA).",
     ''),
    ('formatting', 'Formatting Assistance',
     # Taxonomy description
     "AI can ensure your paper adheres to specific formatting guidelines required "
     "by journals or institutions.",
     ''),
    ('peer_review', 'Peer Review Simulation',
     # Taxonomy description
     "AI can simulate peer review by providing feedback on the strengths and "
     "weaknesses of your paper.",
     ''),
    ('content_enhancement', 'Content Enhancement',
     # Taxonomy description
     "AI can suggest additional content or research that could strengthen your "
     "arguments.",
     ''),
]

_GENAI_KEY_TO_LABEL = {k: lbl for k, lbl, *_ in GENAI_USES}


def _print_genai_table() -> None:
    """Print GENAI_USES as a numbered 3-column table (label | acceptable | unacceptable)."""
    term_w = min(shutil.get_terminal_size((100, 24)).columns, 140)
    num_w  = 3   # "N. "
    lbl_w  = 22  # use-case label (wraps for longer labels)
    gap    = 2
    remain = term_w - num_w - lbl_w - gap * 2
    acc_w  = remain // 2
    rej_w  = remain - acc_w

    rule = "  " + "─" * (num_w + lbl_w + gap + acc_w + gap + rej_w)
    print(f"  {'#':<{num_w}}{'Use Case':<{lbl_w}}  {'Acceptable':<{acc_w}}  Unacceptable")
    print(rule)
    for i, (_, label, acceptable, unacceptable) in enumerate(GENAI_USES, 1):
        lbl_lines = textwrap.wrap(label, lbl_w) or ['']
        acc_lines = textwrap.wrap(acceptable, acc_w) or ['']
        rej_lines = textwrap.wrap(unacceptable, rej_w) or ['']
        n_rows = max(len(lbl_lines), len(acc_lines), len(rej_lines))
        for row in range(n_rows):
            num_s = f"{i}." if row == 0 else ""
            lbl_s = lbl_lines[row] if row < len(lbl_lines) else ""
            acc_s = acc_lines[row] if row < len(acc_lines) else ""
            rej_s = rej_lines[row] if row < len(rej_lines) else ""
            print(f"  {num_s:<{num_w}}{lbl_s:<{lbl_w}}  {acc_s:<{acc_w}}  {rej_s}")
    print(rule)

_NO_AI_TEXT = "The author(s) have not employed any Generative AI tools."

_AI_TEXT_TEMPLATE = (
    "During the preparation of this work, the author(s) used AI ({tools}) for: "
    "{uses}. After using this tool/service, the author(s) reviewed and "
    "edited the content as needed and take(s) full responsibility for the "
    "publication's content."
)


def compose_genai_declaration(uses: list, tools: str) -> str:
    """Build the CEUR-standard GenAI declaration from selected use-case keys."""
    if not uses:
        return _NO_AI_TEXT
    labels   = [_GENAI_KEY_TO_LABEL.get(u, u) for u in uses]
    tool_str = tools.strip() if tools.strip() else 'GenAI tools'
    return _AI_TEXT_TEMPLATE.format(tools=tool_str, uses=', '.join(labels))


def prompt_genai_declaration(settings: dict, settings_path: Path) -> str:
    """Interactively ask the user which GenAI use cases apply; save to JSON."""
    print("\n" + "-" * 60)
    print("  GenAI Declaration")
    print("  See policy: https://ceur-ws.org/GenAI/Policy.html")
    print("-" * 60)
    print("  Did you use GenAI tools when preparing this paper?")
    print("  Enter numbers (space-separated) for all that apply, or Enter for none:\n")
    _print_genai_table()
    print()
    try:
        raw = input("  Selection [Enter = no AI use]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return _NO_AI_TEXT

    selected_keys = []
    if raw:
        for tok in raw.split():
            if tok.isdigit() and 1 <= int(tok) <= len(GENAI_USES):
                selected_keys.append(GENAI_USES[int(tok) - 1][0])

    tools = ''
    if selected_keys:
        try:
            tools = input("  GenAI tool(s) used (e.g. 'ChatGPT, Grammarly'): ").strip()
        except (EOFError, KeyboardInterrupt):
            pass

    decl = compose_genai_declaration(selected_keys, tools)
    settings['genai_uses']        = selected_keys
    settings['genai_tools']       = tools
    settings['genai_declaration'] = decl
    settings_path.write_text(json.dumps(settings, indent=2))

    print(f"\n  Declaration: {decl}")
    print(f"  Saved to {settings_path.name}")
    print("-" * 60 + "\n")
    return decl


def resolve_genai_declaration(settings: dict, settings_path: Path) -> str:
    """Return the effective GenAI declaration, prompting if none is configured."""
    # Explicit text always wins
    if settings.get('genai_declaration', '').strip():
        return settings['genai_declaration'].strip()
    # Compose from saved use-case keys
    if settings.get('genai_uses'):
        return compose_genai_declaration(
            settings['genai_uses'], settings.get('genai_tools', '')
        )
    # Nothing configured — ask interactively if we have a terminal
    if sys.stdin.isatty():
        return prompt_genai_declaration(settings, settings_path)
    # Non-interactive fallback
    return _NO_AI_TEXT


# ---------------------------------------------------------------------------
# LaTeX generation
# ---------------------------------------------------------------------------

def build_author_block(authors: list, affiliations: list) -> list[str]:
    lines = []
    cor_idx = 1
    fn_idx = 1
    for author in authors:
        name = latex_escape(author.get('name', ''))
        affils = author.get('affils', '1')
        attrs = []
        if author.get('orcid'):
            attrs.append(f"orcid={author['orcid']}")
        if author.get('email'):
            attrs.append(f"email={author['email']}")
        if author.get('url'):
            attrs.append(f"url={author['url']}")

        if attrs:
            lines.append(f"\\author[{affils}]{{{name}}}[%")
            for i, a in enumerate(attrs):
                lines.append(a + (',' if i < len(attrs) - 1 else ''))
            lines.append(']')
        else:
            lines.append(f"\\author[{affils}]{{{name}}}")

        if author.get('corresponding'):
            lines.append(f'\\cormark[{cor_idx}]')
            cor_idx += 1
        if author.get('equal_contribution'):
            lines.append(f'\\fnmark[{fn_idx}]')
            fn_idx += 1

    for aff in affiliations:
        lines.append(f"\\address[{aff['num']}]{{{latex_escape(aff['text'])}}}")

    return lines


def build_tex(settings: dict, latex_body: str, images_dir: Path | None) -> str:
    s = settings
    lines = [
        r'\documentclass[]{ceurart}',
        r'\sloppy',
        r'\usepackage{listings}',
        r'\lstset{breaklines=true}',
        r'\usepackage{graphicx}',
        r'\usepackage{longtable}',  # pandoc uses longtable for all tables
        r'\providecommand{\tightlist}{\setlength{\itemsep}{0pt}\setlength{\parskip}{0pt}}',
        # Mirror ceurart's \__make_tbl_caption:nn / \__make_fig_caption:nn styling
        # for captions on longtable/includegraphics, which never enter ceurart's
        # own table/figure float environments (see style_captions()).
        r'\newcommand{\doctablecaption}[2]{\par\noindent'
        r'\parbox{\linewidth}{\rightskip=0pt\sffamily\small\textbf{\color{scolor}#1}\par#2\par}'
        r'\par\vskip4pt}',
        r'\newcommand{\docfigcaption}[2]{\par\noindent'
        r'\parbox{\linewidth}{\rightskip=0pt\sffamily\small\textbf{\color{scolor}#1:}~#2\par}'
        r'\par\vskip6pt}',
    ]
    if images_dir and images_dir.exists():
        # LaTeX wants paths with forward slashes and trailing slash
        img_path = str(images_dir).replace('\\', '/').rstrip('/') + '/'
        lines.append(f'\\graphicspath{{{{{img_path}}}}}')
    lines += ['', r'\begin{document}', '']

    lines.append(f"\\copyrightyear{{{latex_escape(str(s.get('copyrightyear', '2026')))}}}")
    lines.append(f"\\copyrightclause{{{latex_escape(s.get('copyrightclause', ''))}}}")
    lines += ['', f"\\conference{{{latex_escape(s.get('conference', ''))}}}", '']

    lines.append(f"\\title{{{latex_escape(s.get('title', ''))}}}")
    if s.get('title_note'):
        lines += [r'\tnotemark[1]', f"\\tnotetext[1]{{{latex_escape(s['title_note'])}}}"]
    lines.append('')

    lines += build_author_block(s.get('authors', []), s.get('affiliations', []))
    lines.append('')

    authors = s.get('authors', [])
    if any(a.get('corresponding') for a in authors) and s.get('cortext'):
        lines.append(f"\\cortext[1]{{{latex_escape(s['cortext'])}}}")
    if any(a.get('equal_contribution') for a in authors):
        lines.append(r'\fntext[1]{These authors contributed equally.}')
    lines.append('')

    abstract = s.get('abstract', '').strip()
    if abstract:
        lines += [r'\begin{abstract}', abstract, r'\end{abstract}', '']

    keywords = s.get('keywords', [])
    if keywords:
        kw_line = r' \sep '.join(latex_escape(k) for k in keywords)
        lines += [r'\begin{keywords}', kw_line, r'\end{keywords}', '']

    lines += [r'\maketitle', '', latex_body.strip(), '']

    ack = s.get('acknowledgments', '').strip()
    if ack:
        lines += [r'\begin{acknowledgments}', ack, r'\end{acknowledgments}', '']

    genai = s.get('genai_declaration', '').strip()
    if genai:
        lines += [r'\section*{Declaration on Generative AI}', genai, '']

    bib = s.get('bib_file', '').strip()
    if bib:
        lines += [f'\\bibliography{{{bib}}}', '']

    lines.append(r'\end{document}')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Run summary
# ---------------------------------------------------------------------------

def print_summary(settings: dict, abstract_md: str) -> None:
    W = 70
    print("\n" + "=" * W)
    print("  PAPER SETTINGS SUMMARY")
    print("=" * W)

    print(f"\n  Title      : {settings.get('title', '')}")
    print(f"  Conference : {settings.get('conference', '')}")
    print(f"  Year       : {settings.get('copyrightyear', '')}")

    print(f"\n  Authors ({len(settings.get('authors', []))}):")
    for a in settings.get('authors', []):
        tags = []
        if a.get('corresponding'):
            tags.append('corresponding')
        if a.get('orcid'):
            tags.append(f"ORCID {a['orcid']}")
        tag_str = f"  [{', '.join(tags)}]" if tags else ''
        print(f"    [{a.get('affils','?')}] {a['name']}{tag_str}")

    print(f"\n  Affiliations ({len(settings.get('affiliations', []))}):")
    for af in settings.get('affiliations', []):
        print(f"    {af['num']}. {af['text']}")

    kw = settings.get('keywords', [])
    print(f"\n  Keywords   : {', '.join(kw)}" if kw else "\n  Keywords   : (none)")

    src = "from doc" if abstract_md else "from settings"
    text = abstract_md or settings.get('abstract', '')
    if text:
        preview = text[:200].replace('\n', ' ')
        if len(text) > 200:
            preview += ' ...'
        print(f"\n  Abstract ({src}, {len(text)} chars):")
        print(f"    {preview}")
    else:
        print("\n  Abstract   : (not set)")

    genai_uses = settings.get('genai_uses', [])
    genai = settings.get('genai_declaration', '').strip()
    if genai_uses:
        use_labels = [_GENAI_KEY_TO_LABEL.get(u, u) for u in genai_uses]
        tools = settings.get('genai_tools', '') or 'unspecified tools'
        print(f"\n  GenAI uses : {', '.join(use_labels)}")
        print(f"  GenAI tools: {tools}")
    elif genai:
        print(f"\n  GenAI      : {genai[:80]}{'...' if len(genai) > 80 else ''}")

    print("=" * W + "\n")


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------

def find_latex_engine() -> str | None:
    for eng in ['tectonic', 'pdflatex', 'xelatex', 'lualatex']:
        if shutil.which(eng):
            return eng
    return None


# PNG-based replacements for fontawesome5 icons used in the CEURART footer.
# fontawesome5 aborts tectonic (XeTeX) when loading TU-encoding fd files.
# email.png and orcid.png (from the script folder) are copied to tmpdir and
# referenced via \includegraphics, keeping nologo=false so the real icon
# positions in ceurart.cls are used.
_FA_ICON_STUBS = r"""
\ExplSyntaxOff
%% PNG icon stubs (fontawesome5 skipped — TU-font abort in tectonic)
\RequirePackage{graphicx}
\newcommand{\ceur@orcidicon}{\raisebox{-.1ex}{\includegraphics[height=0.85em]{orcid.png}}\ }
\newcommand{\ceur@emailicon}{\raisebox{-.1ex}{\includegraphics[height=0.80em]{email.png}}\ }
\newcommand{\ceur@globeicon}{\textsc{url:\space}}
\newcommand{\ceurfa@dispatch}[1]{%
  \def\ceurfa@t{#1}%
  \ifx\ceurfa@t\ceurfa@eo\ceur@emailicon\else
  \ifx\ceurfa@t\ceurfa@gl\ceur@globeicon\else
  \ifx\ceurfa@t\ceurfa@or\ceur@orcidicon\else
  [\texttt{#1}]%
  \fi\fi\fi}
\def\ceurfa@eo{envelope-open}%
\def\ceurfa@gl{globe}%
\def\ceurfa@or{orcid}%
\newcommand{\faIcon}[2][]{\ceurfa@dispatch{#2}}%
\ExplSyntaxOn
\bool_gset_false:N \g_ceur_nologo_bool
"""


def patch_ceurart_for_tectonic(cls_file: Path) -> None:
    """Patch ceurart.cls so tectonic (XeTeX) can compile it.

    Two packages in ceurart.cls are incompatible with tectonic:
    1. pdfx.sty — requires pdfTeX primitives absent in XeTeX; cls already has a
       hyperref-only fallback, so we make that unconditional.
    2. fontawesome5.sty — aborts tectonic when loading TU-encoding fd files.
       We replace the entire block with TikZ-drawn stubs for the three icons
       ceurart uses (envelope, globe, ORCID iD) and keep nologo=false so the
       proper icon positions in the footer are used.
    """
    text = cls_file.read_text(encoding='utf-8')
    changed = []

    # 1. Replace pdfx conditional with unconditional hyperref load
    patched, n = re.subn(
        r'\\file_if_exist:nTF \{ pdfx\.sty \} \{%.*?\}\{%\n  \\RequirePackage\[unicode\]\{hyperref\}\n\}',
        r'\\RequirePackage[unicode]{hyperref}',
        text,
        flags=re.DOTALL,
    )
    if n:
        changed.append('removed pdfx (XeTeX incompatible)')
        text = patched

    # 2. Replace fontawesome5 block with TikZ icon stubs
    patched, n = re.subn(
        r'\\file_if_exist:nTF \{ fontawesome5\.sty \} \{%.*?\}\{%\n  \\bool_gset_true:N \\g_ceur_nologo_bool\n\}',
        lambda _: _FA_ICON_STUBS,
        text,
        flags=re.DOTALL,
    )
    if n:
        changed.append('replaced fontawesome5 with PNG icons (email.png / orcid.png)')
        text = patched

    if not changed:
        print("  Warning: could not patch ceurart.cls — expected blocks not found", file=sys.stderr)
    else:
        cls_file.write_text(text, encoding='utf-8')
        print(f"  Patched ceurart.cls: {'; '.join(changed)}")


def compile_with_latex(tex_file: Path, engine: str, work_dir: Path) -> Path | None:

    if engine == 'tectonic':
        cls = work_dir / 'ceurart.cls'
        if cls.exists():
            patch_ceurart_for_tectonic(cls)

    def run_engine():
        if engine == 'tectonic':
            return subprocess.run(['tectonic', str(tex_file)],
                                  capture_output=True, text=True, cwd=work_dir)
        return subprocess.run(
            [engine, '-interaction=nonstopmode', '-output-directory', str(work_dir), str(tex_file)],
            capture_output=True, text=True, cwd=work_dir
        )

    print(f"  Compiling with {engine} ...")
    res = run_engine()
    if engine != 'tectonic':
        # Second pass for cross-references
        run_engine()

    pdf = work_dir / (tex_file.stem + '.pdf')
    if not pdf.exists():
        # Show full stdout + stderr so errors are never silently swallowed
        output = (res.stdout + res.stderr).strip()
        if output:
            print(output, file=sys.stderr)
        else:
            print("  (no output from engine — check the .tex file with --keep-tex)",
                  file=sys.stderr)
        return None
    return pdf


def find_libreoffice() -> str | None:
    """Return the soffice binary path, checking common macOS and Linux locations."""
    candidates = [
        LIBREOFFICE,
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        shutil.which("soffice"),
        shutil.which("libreoffice"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


def compile_with_libreoffice(input_file: Path, work_dir: Path) -> Path | None:
    """Convert DOCX to PDF using LibreOffice headless.

    On macOS, LibreOffice may already be running as a GUI app. Using
    -env:UserInstallation gives the headless instance its own profile so
    it does not try to delegate to the GUI process.
    """
    lo = find_libreoffice()
    if not lo:
        print("  Error: LibreOffice not found.", file=sys.stderr)
        print("  Install from https://www.libreoffice.org/download/", file=sys.stderr)
        return None

    profile_dir = work_dir / "lo-profile"
    profile_dir.mkdir(exist_ok=True)
    profile_uri = profile_dir.as_uri()

    cmd = [
        lo,
        f'-env:UserInstallation={profile_uri}',
        '--headless', '--norestore',
        '--convert-to', 'pdf',
        '--outdir', str(work_dir),
        str(input_file),
    ]
    print(f"  Converting to PDF via LibreOffice ...")
    print(f"  Input : {input_file}")
    print(f"  Outdir: {work_dir}")

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        print(f"  Error: could not launch {lo}", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired:
        print("  Error: LibreOffice timed out after 120 s", file=sys.stderr)
        return None

    print(f"  LibreOffice exit code: {res.returncode}")
    if res.stdout.strip():
        print(f"  stdout: {res.stdout.strip()}")
    if res.stderr.strip():
        print(f"  stderr: {res.stderr.strip()}", file=sys.stderr)

    pdf = work_dir / (input_file.stem + '.pdf')
    if not pdf.exists():
        print(f"  Error: expected PDF not found at {pdf}", file=sys.stderr)
        # List what IS in work_dir to help diagnose
        files = list(work_dir.iterdir())
        print(f"  Files in work dir: {[f.name for f in files]}", file=sys.stderr)
        return None

    print(f"  PDF created: {pdf.name} ({pdf.stat().st_size:,} bytes)")
    return pdf


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Convert Google Doc to CEURART PDF',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('-i', '--input', metavar='URL',
                        help='Google Doc URL — required when paper_config.json is missing '
                             'or doc_url is not set; updates doc_url if already set')
    parser.add_argument('--engine', choices=['auto', 'tex', 'lo'], default='auto',
                        help='PDF engine: auto (prefer LaTeX), tex, lo (LibreOffice)')
    parser.add_argument('-f', '--folder', default='temp',
                        help='Working folder for settings, output PDF, and paper.tex '
                             '(relative to script dir; default: temp)')
    parser.add_argument('--keep-tex', action='store_true',
                        help='Save intermediate paper.tex in the working folder')
    parser.add_argument('--settings', type=Path, default=None,
                        help='Override settings JSON path (default: <folder>/paper_config.json)')
    parser.add_argument('-e', '--extract', action='store_true',
                        help='Re-extract title/authors/affiliations/keywords from the doc '
                             'and overwrite paper_config.json (use after doc front-matter changes)')
    args = parser.parse_args()

    # Resolve working folder (relative to script dir)
    work_folder = (SCRIPT_DIR / args.folder).resolve()
    work_folder.mkdir(parents=True, exist_ok=True)

    settings_path = args.settings if args.settings else work_folder / 'paper_config.json'

    # ---- First-time setup or missing doc_url ----
    if not settings_path.exists():
        if not args.input:
            sys.exit(
                f"No paper_config.json found in: {work_folder}\n"
                f"Use -i <google-doc-url> to set up a new paper in this folder."
            )
        print(f"\nFirst-time setup in: {work_folder}")
        print(f"Document URL       : {args.input}")
        settings = dict(DEFAULT_SETTINGS)
        settings['doc_url'] = args.input
        settings_path.write_text(json.dumps(settings, indent=2))
        print(f"Created {settings_path.name}")
        print("Proceeding with first download — edit paper_config.json to fill in "
              "conference, copyright, and other metadata.\n")
    else:
        with open(settings_path) as f:
            settings = json.load(f)
        for k, v in DEFAULT_SETTINGS.items():
            settings.setdefault(k, v)

        if not settings.get('doc_url'):
            if not args.input:
                sys.exit(
                    f"doc_url is not set in {settings_path.name}.\n"
                    f"Use -i <google-doc-url> to specify the document."
                )
            print(f"\nSetting doc_url: {args.input}")
            settings['doc_url'] = args.input
            settings_path.write_text(json.dumps(settings, indent=2))
            print(f"Saved to {settings_path.name}\n")
        elif args.input and args.input != settings.get('doc_url'):
            print(f"\nUpdating doc_url:")
            print(f"  was: {settings['doc_url']}")
            print(f"  now: {args.input}")
            settings['doc_url'] = args.input
            settings_path.write_text(json.dumps(settings, indent=2))

    print(f"\nFolder  : {work_folder}")
    print(f"Title   : {settings.get('title', '(not set)')}")
    print(f"Source  : {settings['doc_url']}")
    print(f"Output  : {work_folder / settings['output_pdf']}")

    with tempfile.TemporaryDirectory() as _tmp:
        tmpdir = Path(_tmp)
        images_dir = tmpdir / "media"   # pandoc --extract-media creates <dir>/media/

        # 1. Download
        print("\n[1/4] Downloading ...")
        docx_file = download_doc(
            settings['doc_url'], settings['export_format'], tmpdir
        )

        # 2. Convert to markdown and extract front matter
        print("\n[2/4] Extracting content ...")
        extra = settings.get('pandoc_extra_args', [])
        # --extract-media must be on the DOCX→markdown step so pandoc can pull
        # images out of the DOCX zip; passing it on a markdown stdin step does nothing.
        # Force pipe tables: multiline/grid tables encode column boundaries as fixed
        # character positions, which desync (bleeding text into the wrong column)
        # once clean_markdown() shortens cell text by stripping {.underline} spans.
        md_text = run_pandoc(
            [str(docx_file),
             '-t', 'markdown-grid_tables-multiline_tables-simple_tables+pipe_tables',
             '--wrap=none', f'--extract-media={tmpdir}'] + extra
        )
        md_text = clean_markdown(md_text)
        md_text = linkify_curies(md_text)

        abstract_md = ''
        if settings.get('abstract_from_doc', True):
            abstract_md, md_text = extract_section(
                md_text, settings.get('abstract_heading', 'Abstract')
            )
            if abstract_md:
                print(f"  Found abstract ({len(abstract_md)} chars)")
        elif settings.get('remove_abstract_from_body', True):
            _, md_text = extract_section(
                md_text, settings.get('abstract_heading', 'Abstract')
            )

        if settings.get('remove_keywords_from_body', True):
            _, md_text = extract_section(
                md_text, settings.get('keywords_heading', 'Keywords')
            )

        # Split off the repeated front-matter block (title/authors/affiliations/ORCIDs)
        # that Google Docs includes at the top of the body before the Introduction.
        preamble, md_text = split_preamble(md_text)
        if preamble:
            meta = extract_doc_metadata(preamble)
            apply_doc_metadata(settings, meta, settings_path, force=args.extract)
            print(f"  Stripped {len(preamble)} chars of repeated front-matter from body")

        # Resolve GenAI declaration before summary (may prompt interactively)
        settings['genai_declaration'] = resolve_genai_declaration(settings, settings_path)

        print_summary(settings, abstract_md)

        # 3. Determine engine
        engine_pref = args.engine if args.engine != 'auto' else settings.get('pdf_engine', 'auto')
        latex_engine = find_latex_engine()
        use_latex = (engine_pref == 'tex') or (engine_pref == 'auto' and latex_engine is not None)
        if engine_pref == 'tex' and not latex_engine:
            sys.exit(
                "No LaTeX engine found. Install tectonic: brew install tectonic\n"
                "or use --engine lo for LibreOffice."
            )

        if use_latex:
            print(f"\n[3/4] Converting body to LaTeX (engine: {latex_engine}) ...")

            # Convert abstract markdown → LaTeX
            if abstract_md:
                abstract_latex = run_pandoc(
                    ['-f', 'markdown', '-t', 'latex', '--wrap=none'],
                    input_text=abstract_md
                ).strip()
            else:
                abstract_latex = settings.get('abstract', '').strip()
            settings['abstract'] = abstract_latex

            # Convert body markdown → LaTeX fragment (no standalone)
            latex_body = run_pandoc(
                ['-f', 'markdown', '-t', 'latex', '--wrap=none'],
                input_text=md_text
            )
            latex_body = merge_adjacent_tables(latex_body)
            latex_body = style_captions(latex_body)

            # Build full .tex
            tex_content = build_tex(settings, latex_body, images_dir)
            tex_file = tmpdir / 'paper.tex'
            tex_file.write_text(tex_content, encoding='utf-8')

            if args.keep_tex:
                kept = work_folder / 'paper.tex'
                shutil.copy(tex_file, kept)
                print(f"  Kept intermediate LaTeX: {kept}")

            # Copy support files into tmpdir so the engine finds them
            for src in [CEURART_CLS, CEURART_BST]:
                if src.exists():
                    shutil.copy(src, tmpdir / src.name)
            for ext in ('*.pdf', '*.png', '*.jpg'):
                for img in SCRIPT_DIR.glob(ext):
                    if img.name not in ('paper.pdf',):
                        shutil.copy(img, tmpdir / img.name)

            print(f"\n[4/4] Compiling ...")
            pdf = compile_with_latex(tex_file, latex_engine, tmpdir)

        else:
            print("\n[3-4/4] Converting with LibreOffice ...")
            if latex_engine is None:
                print("  Tip: install tectonic for full CEURART layout: brew install tectonic")
            # Pass the downloaded DOCX directly — LibreOffice reads it natively.
            # Note: this preserves the Google Doc's own formatting, not the CEURART
            # LaTeX layout. For true CEURART output, install tectonic and re-run.
            pdf = compile_with_libreoffice(docx_file, tmpdir)

        if pdf and pdf.exists():
            out = work_folder / settings['output_pdf']
            shutil.copy(pdf, out)
            print(f"\nDone. PDF saved to:\n  {out}")
        else:
            print("\nConversion failed — see messages above.", file=sys.stderr)
            sys.exit(1)


if __name__ == '__main__':
    main()
