#!/usr/bin/env python3
"""
robot_validator.py

Pre-flight validator for ROBOT template TSV files. Catches issues that would
cause the `robot template` command to fail, including:

  1. Blank lines (\\n...\\n) inside quoted cells in template columns - may break TSV parsers
  2. Single newlines (\\n) inside cells in template columns - may cause issues
  3. Values in parent/EquivalentTo/restriction columns (SC %/EC %) that are
     neither a valid CURIE/IRI nor a known ontology label
  4. Manchester expressions with a missing leading single quote

Only rows with a non-empty Ontology ID (column 2) are validated, and only cells
in columns that carry a ROBOT template directive (row 2) are checked.  Labels
defined within the TSV itself are recognised as valid lookup targets.

Label index / cache strategy
-----------------------------
The script derives a cache filename from the --ontology argument:
  foodon-edit.ofn  →  foodon-edit_cached_merge.ofn  (same directory)

Default (no cache present): directly parse the main OFN file and all imports
  listed in the catalog for rdfs:label annotations.  No cache is written.

Cache present: the existing cache is loaded instead of the ontology files,
  which is faster for large import sets.

--freshen: rebuild the cache via `robot merge` + label extraction, then load
  it.  Use this after adding new terms or imports.  If `robot` is not on PATH,
  falls back to direct OFN parsing without writing a cache.

Usage (run from src/ontology/):
  python3 scripts/robot_validator.py \\
      --tsv ../templates/robot_process.tsv \\
      --ontology foodon-edit.ofn \\
      --catalog catalog-v001.xml

Force a cache rebuild:
  python3 scripts/robot_validator.py ... --freshen

Auto-fix issues that have a deterministic correction:
  python3 scripts/robot_validator.py ... --update
"""

import argparse
import csv
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    from rapidfuzz import process as fuzz_process, fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    import difflib
    HAS_RAPIDFUZZ = False


# ─── Constants ────────────────────────────────────────────────────────────────

# rdfs:label annotation in OFN format, e.g.:
#   AnnotationAssertion(rdfs:label obo:FOODON_00002454 "food material by characteristic"@en)
LABEL_RE = re.compile(
    r'AnnotationAssertion\s*\(\s*rdfs:label\s+\S+\s+"([^"]+)"'
)

# A CURIE looks like PREFIX:localname with no spaces before the colon
CURIE_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_.]*:[^\s]+$')

# Sentinels used to signal malformed-expression types through extract_label_refs
_MALFORMED_MISSING_LEAD = '\x00MISSING_LEAD\x00'
_MALFORMED_UNPAIRED     = '\x00UNPAIRED\x00'

# Manchester OWL class expression keywords that are NOT labels
MANCHESTER_KEYWORDS = {
    'some', 'all', 'only', 'and', 'or', 'not', 'exactly',
    'min', 'max', 'value', 'Self', 'inverse', 'that',
}


# ─── Label Cache ──────────────────────────────────────────────────────────────

def cache_path_for(ontology_file: str) -> Path:
    """
    Derive the label-cache path from the ontology filename.
    E.g.  foodon-edit.ofn  →  foodon-edit_cached_merge.ofn  (same directory).
    """
    p = Path(ontology_file).resolve()
    return p.parent / f'{p.stem}_cached_merge.ofn'


def _robot_available() -> bool:
    """Return True if the `robot` command is on PATH."""
    try:
        subprocess.check_output(['robot', '--version'], stderr=subprocess.STDOUT)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False



def build_label_cache(ontology_file: str, catalog_file: str | None,
                       cache_path: Path) -> None:
    """
    Build a lightweight label cache (.ofn) by running `robot merge` to produce
    a fully resolved ontology, then extracting only rdfs:label annotation
    assertions into the cache file.

    The resulting file is much smaller than a full merge (~10% of size) while
    still providing complete label coverage across all imported ontologies.
    """
    print(f'  Building label cache via robot merge...', file=sys.stderr)

    cmd = ['robot']
    if catalog_file:
        cmd += ['--catalog', catalog_file]

    # Merge to a temp OFN file, then filter; temp file is discarded afterwards
    with tempfile.NamedTemporaryFile(suffix='.ofn', delete=False) as tf:
        tmp_path = tf.name

    try:
        cmd += ['merge', '--input', ontology_file, '--output', tmp_path]
        subprocess.check_output(cmd, stderr=subprocess.STDOUT)

        label_lines: list[str] = []
        with open(tmp_path, encoding='utf-8') as fh:
            for line in fh:
                if LABEL_RE.search(line):
                    label_lines.append(line.rstrip())

    except subprocess.CalledProcessError as e:
        print(f'  ERROR: robot merge failed:\n{e.output.decode()}', file=sys.stderr)
        sys.exit(1)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # Write a minimal valid OFN file containing only label assertions
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, 'w', encoding='utf-8') as fh:
        fh.write('Prefix(rdfs:=<http://www.w3.org/2000/01/rdf-schema#>)\n')
        fh.write('Ontology(<http://purl.obolibrary.org/obo/foodon/label-cache>\n')
        for line in label_lines:
            fh.write(line + '\n')
        fh.write(')\n')

    size_kb = cache_path.stat().st_size // 1024
    print(f'  Wrote {len(label_lines)} label assertions '
          f'({size_kb} KB) → {cache_path}', file=sys.stderr)


def load_labels_from_ofn(filepath: str) -> dict:
    """
    Parse rdfs:label AnnotationAssertions from an OWL Functional Syntax file.
    Returns {lowercase_label: (original_label, source_filename)}.
    """
    result = {}
    try:
        with open(filepath, encoding='utf-8') as fh:
            for m in LABEL_RE.finditer(fh.read()):
                label = m.group(1)
                result[label.lower()] = (label, os.path.basename(filepath))
    except OSError as e:
        print(f'  WARNING: cannot read {filepath}: {e}', file=sys.stderr)
    return result


def parse_catalog(catalog_path: str) -> dict:
    """Parse a catalog-v001.xml and return {uri → absolute_local_path}."""
    catalog_dir = os.path.dirname(os.path.abspath(catalog_path))
    mappings = {}
    ns = 'urn:oasis:names:tc:entity:xmlns:xml:catalog'
    try:
        tree = ET.parse(catalog_path)
        for elem in tree.getroot().iter(f'{{{ns}}}uri'):
            name = elem.get('name', '')
            local = elem.get('uri', '')
            mappings[name] = os.path.normpath(os.path.join(catalog_dir, local))
    except Exception as e:
        print(f'  WARNING: cannot parse catalog {catalog_path}: {e}', file=sys.stderr)
    return mappings


def build_label_index_from_files(ontology_file: str, catalog_file: str | None) -> dict:
    """
    Fallback when `robot` is not available: directly parse the main OFN file
    and every import listed in the catalog for rdfs:label annotations.
    Returns {lowercase_label: (original_label, source_filename)}.
    """
    index = {}
    loaded: set[str] = set()

    def load(path: str) -> None:
        abspath = os.path.abspath(path)
        if abspath in loaded:
            return
        if not os.path.exists(abspath):
            print(f'  WARNING: file not found, skipping: {path}', file=sys.stderr)
            return
        loaded.add(abspath)
        new = load_labels_from_ofn(abspath)
        print(f'  {len(new):>5} labels  {os.path.relpath(abspath)}', file=sys.stderr)
        index.update(new)

    load(ontology_file)
    if catalog_file:
        for _uri, local_path in parse_catalog(catalog_file).items():
            load(local_path)

    return index


def get_label_index(ontology_file: str, catalog_file: str | None,
                     freshen: bool) -> dict:
    """
    Return a {lowercase_label: (original_label, source_filename)} index.

    The cache path is derived from the ontology filename:
      foodon-edit.ofn  →  foodon-edit_cached_merge.ofn  (same directory)

    Strategy:
      - --freshen:
          · robot available → robot merge + label-filter → write cache, then load.
          · robot not found → warn; direct OFN parsing (no cache written).
      - Cache exists (and not --freshen) → load cache.
      - Cache missing (and not --freshen) → direct OFN parsing via catalog.
    """
    cache = cache_path_for(ontology_file)

    if freshen:
        if _robot_available():
            build_label_cache(ontology_file, catalog_file, cache)
        else:
            print('  robot not found — cannot build cache; falling back to direct OFN parsing.',
                  file=sys.stderr)
            return build_label_index_from_files(ontology_file, catalog_file)
    elif not cache.exists():
        print('  No cache found — parsing ontology and imports directly.',
              file=sys.stderr)
        return build_label_index_from_files(ontology_file, catalog_file)

    print(f'  Loading label cache: {cache}', file=sys.stderr)
    index = load_labels_from_ofn(str(cache))
    print(f'  {len(index)} labels loaded from cache.', file=sys.stderr)
    return index


def add_tsv_labels(tsv_file: str, label_index: dict) -> None:
    """
    Add labels defined within the TSV itself to the label index so that rows
    later in the file can reference them as parent/restriction targets.
    Only rows with a non-empty Ontology ID are considered.
    The ID and label columns are detected from the template row (row 2):
      ID column    → directive exactly 'ID'
      Label column → directive starting with 'AL rdfs:label'
    Modifies label_index in-place.
    """
    with open(tsv_file, newline='') as fh:
        rows = list(csv.reader(fh, delimiter='\t'))

    if len(rows) < 2:
        return

    template = rows[1]
    id_col    = next((i for i, t in enumerate(template) if t.strip() == 'ID'), None)
    label_col = next((i for i, t in enumerate(template)
                      if t.strip().startswith('AL rdfs:label')), None)

    if id_col is None or label_col is None:
        return  # can't identify columns; skip silently

    added = 0
    for row in rows[2:]:  # skip header + template rows
        row_id    = row[id_col].strip()    if id_col    < len(row) else ''
        row_label = row[label_col].strip() if label_col < len(row) else ''
        if row_id and row_label:
            key = row_label.lower()
            if key not in label_index:
                label_index[key] = (row_label, os.path.basename(tsv_file))
                added += 1

    print(f'  {added:>5} labels  (from TSV itself)', file=sys.stderr)


# ─── Value Parsing ────────────────────────────────────────────────────────────

def is_curie_or_iri(text: str) -> bool:
    """Return True if text looks like a CURIE (PREFIX:local) or full IRI <...>."""
    text = text.strip()
    return bool(CURIE_RE.match(text)) or text.startswith('<')


def extract_label_refs(cell_value: str, template_directive: str) -> list[str]:
    """
    Given a cell value and its ROBOT template directive, return the list of
    tokens (labels and CURIEs/IRIs) to be validated, plus sentinel strings
    for malformed expressions.

    ROBOT template directives that do label lookups:
      SC %               → parent class: entire value is a plain label or CURIE
      SC % SPLIT=|       → same, pipe-delimited list of labels/CURIEs
      EC %               → equivalent class Manchester expression; class names
                           are wrapped in single quotes: 'label' some 'label'
      SC 'prop' some %   → restriction: % value is a plain label or CURIE

    Key rule: a value is treated as a Manchester expression ONLY if it contains
    single-quoted strings ('...'). Without single quotes, the entire (post-split)
    value is a plain label or CURIE — even if it contains words like "or" or
    "and" that happen to be Manchester keywords (e.g. "meat, poultry or fish quality").

    All plain (non-Manchester) tokens are returned — both CURIEs and labels.
    The caller is responsible for checking each token against the appropriate
    index: CURIEs/IRIs are accepted by syntax; labels are checked against the
    label index.

    Returned sentinels (for malformed expressions):
      _MALFORMED_MISSING_LEAD + original  → missing leading ' (auto-fixable)
      _MALFORMED_UNPAIRED     + original  → quote unpaired elsewhere
    """
    value = cell_value.strip()
    if not value:
        return []

    # Apply SPLIT=| first; each part is resolved independently
    parts = ([p.strip() for p in value.split('|') if p.strip()]
             if 'SPLIT=|' in template_directive
             else [value])

    tokens: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue

        if "'" in part:
            # Contains single-quoted strings → Manchester expression.
            if part.count("'") % 2 != 0:
                # Odd number of quotes: determine if the leading ' is simply missing
                if not part.startswith("'"):
                    tokens.append(_MALFORMED_MISSING_LEAD + part)
                else:
                    tokens.append(_MALFORMED_UNPAIRED + part)
                continue
            # Extract the quoted label tokens; skip pure Manchester keywords
            quoted = [q.strip() for q in re.findall(r"'([^']+)'", part)]
            tokens.extend(
                q for q in quoted
                if q and q.lower() not in MANCHESTER_KEYWORDS
            )
        else:
            # Plain value: a label or CURIE — both are valid in SC/EC % columns.
            # Parentheses are part of the label, e.g. "food (preserved)".
            tokens.append(part)

    return tokens


# ─── Fuzzy Matching ───────────────────────────────────────────────────────────

def find_close_matches(query: str, label_index: dict,
                        n: int = 4, cutoff: float = 0.72) -> list[str]:
    """Return up to n close label matches for query using fuzzy string matching."""
    q = query.lower()
    if HAS_RAPIDFUZZ:
        hits = fuzz_process.extract(
            q, label_index.keys(),
            scorer=fuzz.token_sort_ratio,
            limit=n,
            score_cutoff=int(cutoff * 100),
        )
        return [label_index[h[0]][0] for h in hits]
    else:
        close = difflib.get_close_matches(q, label_index.keys(), n=n, cutoff=cutoff)
        return [label_index[m][0] for m in close]


# ─── Validation ───────────────────────────────────────────────────────────────

def validate(tsv_file: str, label_index: dict) -> list[dict]:
    """
    Validate a ROBOT template TSV and return a list of issue dicts with keys:
      type       : 'ERROR' or 'WARNING'
      row        : CSV row number (1-based; multiline quoted cells = one row)
      col        : column number (1-based)
      header     : column header name
      msg        : human-readable description
      fix        : suggested fix message (always present)
      context    : repr() of the problematic cell
      _row_idx   : 0-based index into all_rows (only on auto-fixable issues)
      _col_idx   : 0-based column index        (only on auto-fixable issues)
      _fix_value : corrected cell value         (only on auto-fixable issues)
    """
    issues: list[dict] = []

    with open(tsv_file, newline='') as fh:
        all_rows = list(csv.reader(fh, delimiter='\t'))

    if len(all_rows) < 2:
        return [{'type': 'ERROR', 'row': None, 'col': None, 'header': '',
                 'msg': 'TSV has fewer than 2 rows (need header + template row).',
                 'fix': 'Add a template directive row as the second row.'}]

    headers  = all_rows[0]
    template = all_rows[1]

    # ── Check 0a: raw-line scan for unterminated quoted fields ─────────────
    # ROBOT's TSV parser reads the file line-by-line without multiline-cell
    # support.  If a field starts with " but the closing " is on a later line
    # (because Python's csv.writer wrapped a multiline value in "..."), ROBOT
    # raises "Unterminated quoted field at end of CSV line".
    # Detection: split each raw line by \t; any field that starts with " but
    # does not also end with " is unterminated from ROBOT's perspective.
    with open(tsv_file, 'rb') as fh:
        raw_lines = fh.read().split(b'\n')

    for raw_lineno, raw_line in enumerate(raw_lines, start=1):
        fields = raw_line.split(b'\t')
        for col_i, field in enumerate(fields):
            if field.startswith(b'"') and not field.endswith(b'"'):
                col_header = headers[col_i] if col_i < len(headers) else f'col{col_i+1}'
                # Show the first ~60 chars of what's inside the opening quote
                preview = field[1:61].decode('utf-8', 'replace')
                issues.append({
                    'type':    'ERROR',
                    'row':     raw_lineno,
                    'col':     col_i + 1,
                    'header':  col_header,
                    'msg':     ('Unterminated quoted field (raw line): field opens with " '
                                'but has no closing " on the same line — ROBOT will fail '
                                'with "Unterminated quoted field at end of CSV line".'),
                    'fix':     'Remove the embedded newline so the entire cell fits on one raw line.',
                    'context': repr(preview),
                })

    # All columns with ANY template directive — used for newline checks
    template_col_indices: set[int] = {
        i for i, t in enumerate(template) if t.strip()
    }

    # Columns whose directive uses % for label/IRI lookup
    lookup_cols = [
        {
            'index':    i,
            'header':   headers[i] if i < len(headers) else f'col{i+1}',
            'template': t,
        }
        for i, t in enumerate(template)
        if '%' in t and t.lstrip().startswith(('SC', 'EC'))
    ]

    # Columns with AI (Annotation IRI) directive — values must be CURIEs or IRIs
    ai_cols = [
        {
            'index':    i,
            'header':   headers[i] if i < len(headers) else f'col{i+1}',
            'template': t,
        }
        for i, t in enumerate(template)
        if t.strip().startswith('AI ')
    ]

    # Locate the ID column dynamically from the template row (directive == 'ID')
    ID_COL = next((i for i, t in enumerate(template) if t.strip() == 'ID'), None)
    if ID_COL is None:
        issues.append({
            'type': 'ERROR', 'row': 2, 'col': None, 'header': '',
            'msg': 'No column with template directive "ID" found in row 2.',
            'fix': 'Add an "ID" directive to the Ontology ID column in the template row.',
        })
        return issues

    # ── Check 0: detect cells with unintended CSV double-quote quoting ─────
    # A cell whose value begins with a literal '"' causes Python's csv module
    # (and any CSV-aware parser) to treat it as the start of a quoted
    # multi-line field, absorbing all subsequent lines until the next bare '"'.
    # ROBOT's TSV parser does NOT do this — it reads every raw line separately,
    # so ROBOT sees a completely different row structure.  Any cell that
    # contains a tab character is a symptom: the csv module absorbed following
    # TSV rows into that cell.
    for row_num_0, row in enumerate(all_rows, start=1):
        for col_i, cell in enumerate(row):
            col_header = headers[col_i] if col_i < len(headers) else f'col{col_i+1}'
            if '\t' in cell:
                # Tabs inside a cell value mean downstream TSV rows were
                # absorbed by csv quoting.  Report the first embedded tab line.
                first_tab_line = next(
                    (ln for ln in cell.split('\n') if '\t' in ln), ''
                )
                issues.append({
                    'type':    'ERROR',
                    'row':     row_num_0,
                    'col':     col_i + 1,
                    'header':  col_header,
                    'msg':     ('Cell contains embedded tab character(s): the csv parser absorbed '
                                'following TSV rows into this cell; ROBOT will see a different '
                                'row structure and fail.'),
                    'fix':     ('Remove or escape the leading double-quote in this cell so it is '
                                'not treated as a csv quoting character.  '
                                f'First absorbed line: {repr(first_tab_line[:100])}'),
                    'context': repr(cell[:120]) + ('...' if len(cell) > 120 else ''),
                })

    for row_num, row in enumerate(all_rows[2:], start=3):
        row_all_idx = row_num - 1   # 0-based index into all_rows

        row_id = row[ID_COL].strip() if ID_COL < len(row) else ''
        if not row_id:
            continue  # ROBOT ignores rows without an Ontology ID

        # ── Check ID: CURIE / IRI format ────────────────────────────────────
        if not is_curie_or_iri(row_id):
            # Detect common accidents:
            #   "obo/FOODON:00004501" → "FOODON:00004501" (path prefix prepended)
            #   "FOODON;00002971"     → "FOODON:00002971" (semicolon instead of colon)
            m_slash = re.match(r'^[^/]+/([A-Za-z_][A-Za-z0-9_.]*:[^\s]+)$', row_id)
            m_semi  = re.match(r'^([A-Za-z_][A-Za-z0-9_.]*);([^\s]+)$', row_id)
            if m_slash:
                fix_value = m_slash.group(1)
            elif m_semi:
                fix_value = f'{m_semi.group(1)}:{m_semi.group(2)}'
            else:
                fix_value = None
            id_header = headers[ID_COL] if ID_COL < len(headers) else 'ID'
            issue: dict = {
                'type':    'ERROR',
                'row':     row_num,
                'col':     ID_COL + 1,
                'header':  id_header,
                'msg':     f'Malformed ID — not a valid CURIE or IRI: "{row_id}"',
                'fix':     (f'Suggested correction: "{fix_value}"'
                            if fix_value
                            else 'A CURIE must be PREFIX:localname with no path separators '
                                 '(e.g. FOODON:00004501).'),
                'context': repr(row_id),
            }
            if fix_value is not None:
                issue['_row_idx']   = row_all_idx
                issue['_col_idx']   = ID_COL
                issue['_fix_value'] = fix_value
            issues.append(issue)

        # ── Check 1 & 2: newlines in any cell ────────────────────────────────
        # Any embedded newline causes Python's csv.writer to wrap the cell in
        # "...", producing an unterminated quoted field on ROBOT's line-by-line
        # parser.  Check all columns, not just template-directive ones.
        for col_i in range(len(row)):
            if col_i >= len(row):
                continue
            cell = row[col_i]
            if not cell:
                continue

            col_header = headers[col_i] if col_i < len(headers) else f'col{col_i+1}'
            preview = repr(cell[:120]) + ('...' if len(cell) > 120 else '')

            if re.search(r'\n[ \t]*\n', cell):
                fixed = re.sub(r'\n[ \t]*\n', ' ', cell)
                before_nl = cell[:cell.index('\n')]
                if len(before_nl) > 40:
                    before_nl = '...' + before_nl[-37:]
                issues.append({
                    'type':       'WARNING',
                    '_category':  'newline',
                    'row':        row_num,
                    'col':        col_i + 1,
                    'header':     col_header,
                    'msg':        'blank line (\\n...\\n)',
                    'before_nl':  before_nl,
                    '_row_idx':   row_all_idx,
                    '_col_idx':   col_i,
                    '_fix_value': fixed,
                })
            elif '\n' in cell:
                before_nl = cell[:cell.index('\n')]
                if len(before_nl) > 40:
                    before_nl = '...' + before_nl[-37:]
                fixed = cell.replace('\n', ' ').strip()
                issues.append({
                    'type':       'WARNING',
                    '_category':  'newline',
                    'row':        row_num,
                    'col':        col_i + 1,
                    'header':     col_header,
                    'msg':        'embedded newline (\\n)',
                    'before_nl':  before_nl,
                    '_row_idx':   row_all_idx,
                    '_col_idx':   col_i,
                    '_fix_value': fixed,
                })

        # ── Check 3: label/CURIE lookups ───────────────────────────────────
        # SC % and EC % columns accept either a CURIE/IRI (resolved directly
        # by ROBOT) or a plain label (looked up against the ontology).
        for col_info in lookup_cols:
            col_i = col_info['index']
            cell  = row[col_i].strip() if col_i < len(row) else ''
            if not cell:
                continue

            for token in extract_label_refs(cell, col_info['template']):

                # ── Malformed: missing leading quote (auto-fixable) ─────────
                if token.startswith(_MALFORMED_MISSING_LEAD):
                    issues.append({
                        'type':       'ERROR',
                        'row':        row_num,
                        'col':        col_i + 1,
                        'header':     col_info['header'],
                        'msg':        ('Manchester expression is missing a leading single quote: '
                                       f'"{token[len(_MALFORMED_MISSING_LEAD):]}"'),
                        'fix':        "Auto-fixable: will prepend a ' to the cell value.",
                        'context':    repr(cell),
                        '_row_idx':   row_all_idx,
                        '_col_idx':   col_i,
                        '_fix_value': "'" + cell,
                    })
                    continue

                # ── Malformed: unpaired quote elsewhere (not auto-fixable) ──
                if token.startswith(_MALFORMED_UNPAIRED):
                    issues.append({
                        'type':    'ERROR',
                        'row':     row_num,
                        'col':     col_i + 1,
                        'header':  col_info['header'],
                        'msg':     'Manchester expression has an unpaired single quote.',
                        'fix':     'Check that every class label is fully wrapped in single quotes.',
                        'context': repr(cell),
                    })
                    continue

                # ── Valid CURIE or IRI — accepted directly ──────────────────
                if is_curie_or_iri(token):
                    continue

                # ── Known label — accepted ──────────────────────────────────
                if token.lower() in label_index:
                    continue

                # ── Neither CURIE nor known label ───────────────────────────
                suggestions = find_close_matches(token, label_index)
                fix_msg = (
                    'Closest matches: ' + ', '.join(f'"{s}"' for s in suggestions)
                ) if suggestions else 'No close matches found in ontology.'

                issues.append({
                    'type':    'ERROR',
                    'row':     row_num,
                    'col':     col_i + 1,
                    'header':  col_info['header'],
                    'msg':     f'Value is not a valid CURIE/IRI and not a known ontology label: "{token}"',
                    'fix':     fix_msg,
                    'context': repr(cell),
                })

        # ── Check 4: AI column values must be valid CURIEs or IRIs ─────────
        for col_info in ai_cols:
            col_i    = col_info['index']
            raw_cell = row[col_i] if col_i < len(row) else ''
            if not raw_cell:
                continue

            directive = col_info['template']
            is_split  = 'SPLIT=|' in directive
            parts     = raw_cell.split('|') if is_split else [raw_cell]

            # Check for leading/trailing whitespace on any part (auto-fixable)
            stripped_parts = [p.strip() for p in parts]
            has_whitespace = any(p != s for p, s in zip(parts, stripped_parts))
            fixed_cell     = '|'.join(stripped_parts) if is_split else stripped_parts[0]

            if has_whitespace:
                issues.append({
                    'type':       'ERROR',
                    'row':        row_num,
                    'col':        col_i + 1,
                    'header':     col_info['header'],
                    'msg':        'AI column value has leading/trailing whitespace on one or more parts.',
                    'fix':        f'Strip whitespace from each part; corrected value: {repr(fixed_cell)}',
                    'context':    repr(raw_cell[:120]),
                    '_row_idx':   row_all_idx,
                    '_col_idx':   col_i,
                    '_fix_value': fixed_cell,
                })

            # Check each (stripped) part for valid CURIE or IRI format.
            # Exception: a plain-text value that matches a known ontology label
            # is accepted (it functions as a cross-reference by name).
            for part in stripped_parts:
                if not part:
                    continue
                if not is_curie_or_iri(part) and part.lower() not in label_index:
                    issues.append({
                        'type':    'ERROR',
                        'row':     row_num,
                        'col':     col_i + 1,
                        'header':  col_info['header'],
                        'msg':     f'AI column value is not a valid CURIE or IRI, and not a known ontology label: "{part}"',
                        'fix':     ('Values in AI columns must be CURIEs (e.g. IAO:0000119), '
                                    'full IRIs (e.g. https://example.org/...), '
                                    'or a label that exists in the ontology.'),
                        'context': repr(raw_cell[:120]),
                    })

    return issues


# ─── Auto-Fix ─────────────────────────────────────────────────────────────────

def apply_fixes(tsv_file: str, issues: list[dict]) -> int:
    """
    Apply all auto-fixable issues (those with a '_fix_value' key) to the TSV
    file in-place. If two fixes target the same cell the first-encountered wins.
    Returns the number of cells changed.
    """
    # One fix per (row_idx, col_idx); first-encountered wins
    fixes: dict[tuple[int, int], tuple[str, dict]] = {}
    for issue in issues:
        if '_fix_value' not in issue:
            continue
        key = (issue['_row_idx'], issue['_col_idx'])
        if key not in fixes:
            fixes[key] = (issue['_fix_value'], issue)

    if not fixes:
        return 0

    with open(tsv_file, newline='') as fh:
        all_rows = [list(row) for row in csv.reader(fh, delimiter='\t')]

    applied = 0
    for (row_idx, col_idx), (fix_value, _issue) in sorted(fixes.items()):
        if row_idx >= len(all_rows):
            continue
        row = all_rows[row_idx]
        while len(row) <= col_idx:
            row.append('')
        if row[col_idx] != fix_value:
            row[col_idx] = fix_value
            applied += 1

    with open(tsv_file, 'w', newline='') as fh:
        writer = csv.writer(fh, delimiter='\t',
                             quoting=csv.QUOTE_MINIMAL, lineterminator='\n')
        for row in all_rows:
            writer.writerow(row)

    return applied


# ─── Report ───────────────────────────────────────────────────────────────────

def print_report(issues: list[dict], tsv_file: str, update_mode: bool) -> int:
    """
    Print a formatted report.  When update_mode is True, auto-fixable issues
    are tagged [AUTO-FIX] and show the replacement value.
    Returns the number of errors.
    """
    errors        = [i for i in issues if i['type'] == 'ERROR']
    newline_warns = [i for i in issues if i.get('_category') == 'newline']
    other_warns   = [i for i in issues
                     if i['type'] == 'WARNING' and i.get('_category') != 'newline']

    bar = '=' * 64
    print(f'\n{bar}')
    print(f'ROBOT TSV Validation: {tsv_file}')
    print(f'{bar}')
    print(f'{len(errors)} error(s)   {len(newline_warns) + len(other_warns)} warning(s)\n')

    # ── Deduplicate "label not found" errors by label ──────────────────────
    # For repeated labels, keep the first occurrence but append subsequent
    # row numbers to its header line; drop the later duplicates.
    seen_label_msg: dict[str, list[int]] = {}  # msg → [extra row numbers]
    deduped_errors: list[dict] = []
    for issue in errors:
        msg = issue.get('msg', '')
        if msg.startswith('Value is not a valid CURIE') or msg.startswith('Label not found'):
            if msg not in seen_label_msg:
                seen_label_msg[msg] = []
                deduped_errors.append(issue)
            else:
                seen_label_msg[msg].append(issue['row'])
        else:
            deduped_errors.append(issue)

    # ── Errors and non-newline warnings (one block each) ───────────────────
    for issue in deduped_errors + other_warns:
        kind   = issue['type']
        row    = issue.get('row')
        col    = issue.get('col')
        header = issue.get('header', '')
        auto   = '_fix_value' in issue

        if row is not None and col is not None:
            location = f'Row {row}, Col {col} ({header})'
        elif row is not None:
            location = f'Row {row}'
        else:
            location = ''

        # Append extra row numbers for deduplicated label-not-found errors
        extra = seen_label_msg.get(issue.get('msg', ''), [])
        also  = (';  also row ' + ', '.join(str(r) for r in extra)) if extra else ''

        tag = f'[{kind}]' + (' [AUTO-FIX]' if auto and update_mode else '')
        print(f'{tag}  {location}{also}')
        print(f'  {issue["msg"]}')
        if 'fix' in issue:
            print(f'  Fix: {issue["fix"]}')
        if 'context' in issue:
            print(f'  Cell: {issue["context"]}')
        if auto and update_mode:
            print(f'  → Replacing with: {repr(issue["_fix_value"][:120])}')
        print()

    # ── Newline warnings grouped into one section ───────────────────────────
    if newline_warns:
        n_blank    = sum(1 for i in newline_warns if 'blank' in i['msg'])
        n_embedded = len(newline_warns) - n_blank
        parts = []
        if n_blank:
            parts.append(f'{n_blank} blank line(s) (\\n...\\n)')
        if n_embedded:
            parts.append(f'{n_embedded} embedded newline(s) (\\n)')
        parts.append('all auto-fixable with --update')
        print(f'[WARNING]  Cells with newlines: {", ".join(parts)}.')
        print(f'  Cells with embedded newlines may cause issues in some TSV parsers.')
        for i in newline_warns:
            auto_tag = ' [AUTO-FIX]' if '_fix_value' in i and update_mode else ''
            before   = f'  near: "{i["before_nl"]}"' if i.get('before_nl') else ''
            print(f'  Row {i["row"]}, Col {i["col"]} ({i["header"]})'
                  f'  — {i["msg"]}{auto_tag}{before}')
        print()

    if not issues:
        print('  No issues found.')

    return len(errors)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Validate a ROBOT template TSV against an OWL ontology.',
        epilog=(
            'Run from src/ontology/. Exits with code 1 if any errors remain.\n'
            'Label cache: {ontology_stem}_cached_merge.ofn (next to ontology file).'
        ),
    )
    parser.add_argument(
        '--tsv', required=True, metavar='FILE',
        help='ROBOT template TSV file to validate',
    )
    parser.add_argument(
        '--ontology', required=True, metavar='FILE',
        help='Main OWL/OFN ontology file (e.g. foodon-edit.ofn)',
    )
    parser.add_argument(
        '--catalog', metavar='FILE',
        help='OWL catalog XML for import resolution (e.g. catalog-v001.xml)',
    )
    parser.add_argument(
        '--freshen', action='store_true',
        help='Build (or rebuild) the label cache ({ontology_stem}_cached_merge.ofn) '
             'via robot merge. The cache is then used on subsequent runs. '
             'Without this flag, the cache is used if present, otherwise labels '
             'are loaded directly from the ontology and its imports.',
    )
    parser.add_argument(
        '-u', '--update', action='store_true',
        help='Auto-fix issues that have a deterministic correction and rewrite '
             'the TSV file in-place. Fixable issues are: embedded newlines '
             '(\\n and \\n...\\n) in cells, and Manchester expressions with a '
             'missing leading quote.',
    )
    parser.add_argument(
        '--no-warnings', action='store_true',
        help='Suppress warnings; report errors only.',
    )
    args = parser.parse_args()

    # ── Build / load label index ───────────────────────────────────────────
    print('Loading label index...', file=sys.stderr)
    label_index = get_label_index(args.ontology, args.catalog, freshen=args.freshen)

    add_tsv_labels(args.tsv, label_index)
    print(f'  Total unique labels: {len(label_index)}\n', file=sys.stderr)

    # ── Validate ──────────────────────────────────────────────────────────
    issues = validate(args.tsv, label_index)

    if args.no_warnings:
        issues = [i for i in issues if i['type'] != 'WARNING']

    error_count = print_report(issues, args.tsv, update_mode=args.update)

    # ── Apply fixes ────────────────────────────────────────────────────────
    if args.update:
        n = apply_fixes(args.tsv, issues)
        if n:
            print(f'\nApplied {n} auto-fix(es) to: {args.tsv}')
            print('Re-run validation to confirm remaining issues.')
        else:
            print('\nNo auto-fixable issues found.')

    sys.exit(1 if error_count > 0 else 0)


if __name__ == '__main__':
    main()
