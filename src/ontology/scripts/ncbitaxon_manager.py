#!/usr/bin/env python3
"""
Manage NCBITaxon IDs in FoodOn source files: detect replaced IDs, update files,
ensure OntoFox import lists are complete, look up taxa, and check for import gaps.

A replacement is detected when a queried ID appears in the 'secondary_tax_ids'
list of an NCBI API response entry — meaning that ID is now a deprecated alias
for a different primary tax_id.

Supported input file formats (auto-detected by extension):
  .owl / .ofn   OWL ontology file — scans every line for 'NCBITaxon_<digits>'
  .txt          OntoFox input file — reads lines whose first token is a URL
                  containing 'NCBITaxon_<digits>' (comment lines skipped)
                  Name comments on URI lines use the format: # "Scientific Name"
  .tsv          ROBOT template — reads 'NCBITaxon:<digits>' from column 0.
                  If a 'taxon' column is present, its scientific names are also
                  checked against the OntoFox file; missing names are looked up
                  via the NCBI API and added when --update/--update-auto is active.

Usage:
    python ncbitaxon_manager.py [options] [input_file]

    input_file defaults to: imports/ncbitaxon_import.owl

Examples:
    # Check the default OWL import (report only):
    python scripts/ncbitaxon_manager.py

    # Check an OntoFox source list and prompt to update each finding:
    python scripts/ncbitaxon_manager.py --update imports/ncbitaxon_ontofox.txt

    # Check a ROBOT template and apply all replacements automatically:
    python scripts/ncbitaxon_manager.py --update-auto ../templates/robot_seafood.tsv

    # TSV output (clean for piping or saving):
    python scripts/ncbitaxon_manager.py --tsv imports/ncbitaxon_import.owl

    # Use an NCBI API key (10 req/s instead of 3 req/s):
    python scripts/ncbitaxon_manager.py --api-key YOUR_KEY_HERE

    # Check specific IDs only:
    python scripts/ncbitaxon_manager.py --ids 1913631,121025,492052

    # Check a TSV template and ensure all its IDs are in the OntoFox import list:
    python scripts/ncbitaxon_manager.py -i ../templates/robot_seafood.tsv

    # Combine: update replacements AND import missing IDs into ontofox:
    python scripts/ncbitaxon_manager.py --update -i ../templates/robot_seafood.tsv

    # Look up one or more taxon IDs and print a markdown comparison table:
    python scripts/ncbitaxon_manager.py -l 1913631,3049887 --api-key YOUR_KEY_HERE

    # Check for IDs present in ncbitaxon_ontofox.txt but absent from ncbitaxon_import.owl:
    python scripts/ncbitaxon_manager.py --check-missing

    # Sync/fix # "Scientific Name" comments in an OntoFox file:
    python scripts/ncbitaxon_manager.py --sync-names imports/ncbitaxon_ontofox.txt

    # Combine replacement-check and name-sync in one pass:
    python scripts/ncbitaxon_manager.py --update --sync-names imports/ncbitaxon_ontofox.txt
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NCBI_API_BASE  = "https://api.ncbi.nlm.nih.gov/datasets/v2/taxonomy/taxon"
OBO_URI_BASE   = "http://purl.obolibrary.org/obo/NCBITaxon_"
ONTOFOX_PATH   = Path("imports/ncbitaxon_ontofox.txt")
# New terms are inserted in [Low level source term URIs], just before this:
ONTOFOX_INSERT_BEFORE = "[Top level source term URIs and target direct superclass URIs]"

# NCBI allows 3 req/s without a key, 10 req/s with one.
DEFAULT_BATCH_SIZE = 500  # ~3600 char URL; NCBI rejects requests above ~4000 chars
DELAY_NO_KEY   = 0.40    # seconds between requests without API key
DELAY_WITH_KEY = 0.12    # seconds between requests with API key

MAX_RETRIES   = 3
RETRY_BACKOFF = 2.0      # multiply delay by this factor on each retry


# Display order for classification ranks (broad → specific)
_RANK_ORDER = ['domain', 'kingdom', 'phylum', 'class', 'order', 'family', 'genus', 'species']


# ---------------------------------------------------------------------------
# ID extraction — one parser per file format
# ---------------------------------------------------------------------------

_OWL_PATTERN  = re.compile(r'NCBITaxon_(\d+)')
_TSV_COL0     = re.compile(r'^NCBITaxon:(\d+)')
_ONTOFOX_URL  = re.compile(r'^https?://\S*NCBITaxon_(\d+)')
_QUOTED_NAME  = re.compile(r'^"([^"]*)"(.*)')   # "name"[optional trailing notes]


def extract_ids_from_owl(path: Path) -> list[int]:
    """OWL / OFN: every occurrence of 'NCBITaxon_<digits>' on any line."""
    seen = set()
    with path.open('r', encoding='utf-8') as fh:
        for line in fh:
            for m in _OWL_PATTERN.finditer(line):
                seen.add(int(m.group(1)))
    return sorted(seen)


def extract_ids_from_ontofox(path: Path) -> list[int]:
    """
    OntoFox .txt: collect IDs from lines whose first token is a URL containing
    'NCBITaxon_<digits>'.  Comment lines (starting with '#') are skipped.
    """
    seen = set()
    with path.open('r', encoding='utf-8') as fh:
        for line in fh:
            content = line.strip()
            if not content or content.startswith('#'):
                continue
            m = _ONTOFOX_URL.match(content)
            if m:
                seen.add(int(m.group(1)))
    return sorted(seen)


def extract_ids_from_tsv(path: Path) -> list[int]:
    """
    ROBOT template .tsv: collect IDs from column 0 values matching
    'NCBITaxon:<digits>'.  Header/directive rows don't match and are skipped.
    """
    seen = set()
    with path.open('r', encoding='utf-8') as fh:
        for line in fh:
            col0 = line.split('\t', 1)[0].strip()
            m = _TSV_COL0.match(col0)
            if m:
                seen.add(int(m.group(1)))
    return sorted(seen)


def extract_ids_from_file(path: Path) -> tuple[list[int], str]:
    """
    Dispatch to the right parser by file extension.
    Returns (sorted_id_list, format_name).
    """
    suffix = path.suffix.lower()
    if suffix in ('.owl', '.ofn'):
        return extract_ids_from_owl(path), 'OWL'
    if suffix == '.txt':
        return extract_ids_from_ontofox(path), 'OntoFox'
    if suffix == '.tsv':
        return extract_ids_from_tsv(path), 'TSV'
    return extract_ids_from_owl(path), 'unknown (treated as OWL)'


def parse_explicit_ids(id_string: str) -> tuple[list[int], list[str]]:
    """
    Parse a comma-separated string of numeric taxon IDs and/or scientific names.
    Returns ``(sorted_numeric_ids, name_tokens)``; callers are responsible for
    resolving name tokens to IDs via :func:`lookup_names_via_ncbi`.
    """
    ids: list[int] = []
    names: list[str] = []
    for part in id_string.split(','):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
        elif part:
            names.append(part)
    return sorted(set(ids)), names


# ---------------------------------------------------------------------------
# NCBI API — replacement detection
# ---------------------------------------------------------------------------

def _build_url(taxon_ids: list[int], page_size: int, api_key: str | None,
               page_token: str | None = None) -> str:
    id_str = ','.join(str(i) for i in taxon_ids)
    # safe=',' keeps commas unencoded; %2C encoding triples their length and
    # pushes batches of ~600+ IDs over the server's ~4000-char URL limit.
    url = (
        f"{NCBI_API_BASE}/{urllib.parse.quote(id_str, safe=',')}/dataset_report"
        f"?returned_content=METADATA&page_size={page_size}&table_format=SUMMARY"
    )
    if api_key:
        url += f"&api_key={urllib.parse.quote(api_key)}"
    if page_token:
        url += f"&page_token={urllib.parse.quote(page_token)}"
    return url


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={'accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def query_batch(taxon_ids: list[int], api_key: str | None,
                delay: float) -> list[dict]:
    """
    Query the NCBI API for one batch of taxon IDs, following pagination tokens.
    Returns a flat list of all 'taxonomy' dicts from the response.
    """
    results = []
    page_token = None
    page_size = len(taxon_ids)

    while True:
        url = _build_url(taxon_ids, page_size, api_key, page_token)

        retries = 0
        while True:
            try:
                data = _fetch_json(url)
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and retries < MAX_RETRIES:
                    wait = delay * (RETRY_BACKOFF ** retries)
                    print(f"\n  Rate limited — waiting {wait:.1f}s ...",
                          file=sys.stderr)
                    time.sleep(wait)
                    retries += 1
                else:
                    raise

        for report in data.get('reports', []):
            taxonomy = report.get('taxonomy')
            if taxonomy:
                results.append(taxonomy)

        page_token = data.get('next_page_token')
        if not page_token:
            break
        time.sleep(delay)

    return results


def find_replacements(taxon_ids: list[int], api_key: str | None,
                      batch_size: int, verbose: bool) -> list[tuple[int, int, str]]:
    """
    Query NCBI for all taxon_ids in batches and return a sorted list of
    (old_id, new_id, new_name) for every ID that now appears as a secondary
    (deprecated) alias for a different primary tax_id.
    """
    queried  = set(taxon_ids)
    delay    = DELAY_WITH_KEY if api_key else DELAY_NO_KEY
    replacements: dict[int, tuple[int, str]] = {}

    total     = len(taxon_ids)
    n_batches = (total + batch_size - 1) // batch_size

    for batch_num, start in enumerate(range(0, total, batch_size), 1):
        batch = taxon_ids[start:start + batch_size]

        if verbose:
            print(
                f"Batch {batch_num}/{n_batches}: querying {len(batch)} IDs "
                f"({start + 1}–{min(start + batch_size, total)} of {total})",
                file=sys.stderr,
            )
        else:
            pct = 100 * batch_num // n_batches
            print(f"\r  [{pct:3d}%] batch {batch_num}/{n_batches} ...",
                  end='', flush=True, file=sys.stderr)

        try:
            taxonomy_entries = query_batch(batch, api_key, delay)
        except Exception as exc:
            print(f"\nError on batch {batch_num}: {exc}", file=sys.stderr)
            continue

        seen_new_ids: set[int] = set()

        for entry in taxonomy_entries:
            new_id        = entry.get('tax_id')
            secondary_ids = entry.get('secondary_tax_ids', [])
            if not new_id or new_id in seen_new_ids:
                continue
            seen_new_ids.add(new_id)

            name = entry.get('current_scientific_name', {}).get('name', '(unknown)')

            for old_id in secondary_ids:
                if old_id in queried and old_id not in replacements:
                    replacements[old_id] = (new_id, name)

        if batch_num < n_batches:
            time.sleep(delay)

    if not verbose:
        print(file=sys.stderr)

    return sorted(
        (old, new_id, name) for old, (new_id, name) in replacements.items()
    )


# ---------------------------------------------------------------------------
# NCBI API — lookup table
# ---------------------------------------------------------------------------

def _fetch_reports(taxon_ids: list[int], api_key: str | None) -> list[dict]:
    """
    Fetch full dataset_report records for a small list of IDs (no pagination
    needed for lookups of 1–2 taxa).  Uses table_format=SUMMARY to get the
    classification and basionym fields not returned by the batch replacement
    endpoint.
    """
    id_str = ','.join(str(i) for i in taxon_ids)
    url = (
        f"{NCBI_API_BASE}/{urllib.parse.quote(id_str, safe=',')}/dataset_report"
        f"?table_format=SUMMARY"
    )
    if api_key:
        url += f"&api_key={urllib.parse.quote(api_key)}"
    req = urllib.request.Request(url, headers={'accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read()).get('reports', [])


def _fmt_name_auth(name_obj: dict | None, wrap_parens: bool = False) -> str:
    """
    Combine a {name, authority} dict into a display string.
    If wrap_parens is True, parentheses are added around the authority
    when not already present.
    """
    if not name_obj:
        return ''
    name = name_obj.get('name', '') or ''
    auth = name_obj.get('authority', '') or ''
    if auth and wrap_parens and not auth.startswith('('):
        auth = f"({auth})"
    return f"{name} {auth}".strip() if auth else name


def _taxon_link(cls_entry: dict | None) -> str:
    """Return a markdown [name](OBO_URI) link for a classification rank entry."""
    if not cls_entry:
        return ''
    name = cls_entry.get('name', '')
    tid  = cls_entry.get('id')
    if not name or not tid:
        return ''
    return f"[{name}]({OBO_URI_BASE}{tid})"


def _print_lookup_table(queried_ids: list[int], api_key: str | None) -> None:
    """
    Fetch and print a space-padded markdown table for the given taxon IDs.
    One data column per ID; rows cover taxon id, rank, scientific name,
    basionym, group, formal status, secondary tax ids, and classification.
    Best-effort: errors are reported to stderr and the function returns quietly.
    """
    try:
        reports = _fetch_reports(queried_ids, api_key)
    except Exception as exc:
        print(f"  (lookup table unavailable: {exc})", file=sys.stderr)
        return

    # Map each queried ID → its taxonomy dict (first match per query value)
    tax_by_query: dict[int, dict] = {}
    for report in reports:
        tax = report.get('taxonomy', {})
        for q in report.get('query', []):
            try:
                qid = int(q)
                if qid not in tax_by_query:
                    tax_by_query[qid] = tax
            except (ValueError, TypeError):
                pass

    def tax(qid: int) -> dict:
        return tax_by_query.get(qid, {})

    def cell(qid: int, *keys: str) -> str:
        v: object = tax(qid)
        for k in keys:
            if not isinstance(v, dict):
                return ''
            v = v.get(k)
            if v is None:
                return ''
        return str(v) if v is not None else ''

    present_ranks = [
        r for r in _RANK_ORDER
        if any(r in (tax(qid).get('classification') or {}) for qid in queried_ids)
    ]

    rows: list[tuple[str, list[str]]] = []
    rows.append(('taxon id', [cell(qid, 'tax_id') for qid in queried_ids]))
    rows.append(('rank',     [cell(qid, 'rank')    for qid in queried_ids]))
    rows.append(('scientific name', [
        _fmt_name_auth(tax(qid).get('current_scientific_name'))
        for qid in queried_ids
    ]))
    rows.append(('basionym', [
        _fmt_name_auth(
            (tax(qid).get('current_scientific_name') or {}).get('basionym'),
            wrap_parens=True,
        )
        for qid in queried_ids
    ]))
    rows.append(('group',  [cell(qid, 'group_name') for qid in queried_ids]))
    rows.append(('formal', [
        'yes' if tax(qid).get('current_scientific_name_is_formal') else 'no'
        for qid in queried_ids
    ]))
    rows.append(('secondary tax ids', [
        ', '.join(str(i) for i in (tax(qid).get('secondary_tax_ids') or []))
        for qid in queried_ids
    ]))
    rows.append(('**classification**', ['' for _ in queried_ids]))
    for rank in present_ranks:
        rows.append((rank, [
            _taxon_link((tax(qid).get('classification') or {}).get(rank))
            for qid in queried_ids
        ]))

    headers = ['Field'] + [f"NCBITaxon:{qid}" for qid in queried_ids]
    widths  = [len(h) for h in headers]
    for label, cells in rows:
        widths[0] = max(widths[0], len(label))
        for i, c in enumerate(cells):
            widths[i + 1] = max(widths[i + 1], len(c))

    def fmt_row(cells: list[str]) -> str:
        return '| ' + ' | '.join(c.ljust(widths[i]) for i, c in enumerate(cells)) + ' |'

    sep = '| ' + ' | '.join('-' * w for w in widths) + ' |'

    print()
    print(fmt_row(headers))
    print(sep)
    for label, cells in rows:
        print(fmt_row([label] + cells))
    print()


def lookup_taxon(id_string: str, api_key: str | None) -> None:
    """Parse a comma-delimited ID string and print the lookup table, then exit."""
    queried_ids: list[int] = []
    for part in id_string.split(','):
        part = part.strip()
        if part.isdigit():
            queried_ids.append(int(part))
        elif part:
            print(f"Warning: skipping non-numeric token: {part!r}", file=sys.stderr)

    if not queried_ids:
        print("Error: no valid numeric IDs provided.", file=sys.stderr)
        sys.exit(1)

    if not api_key:
        print(
            "Warning: --api-key not provided. NCBI rate limit is ~3 req/s without a key.\n"
            "A free key is available at https://www.ncbi.nlm.nih.gov/account/",
            file=sys.stderr,
        )

    _print_lookup_table(queried_ids, api_key)


# ---------------------------------------------------------------------------
# Output — replacement report
# ---------------------------------------------------------------------------

def report_human(replacements: list[tuple[int, int, str]]) -> None:
    if not replacements:
        print("No replaced IDs found.")
        return

    c1, c2, c3 = 22, 22, 52          # column widths: old, new, uri
    header_bar = '=' * (c1 + c2 + c3 + 20)
    print(f"\n{header_bar}")
    print(f"Found {len(replacements)} replaced NCBITaxon ID(s):")
    print(header_bar)
    print(f"  {'Old ID':<{c1}} {'New ID':<{c2}} {'New URI':<{c3}} Name")
    print(f"  {'-'*(c1-2):<{c1}} {'-'*(c2-2):<{c2}} {'-'*(c3-2):<{c3}} ----")
    for old_id, new_id, name in replacements:
        new_uri = f"{OBO_URI_BASE}{new_id}"
        print(
            f"  {'NCBITaxon_' + str(old_id):<{c1}}"
            f" {'NCBITaxon_' + str(new_id):<{c2}}"
            f" {new_uri:<{c3}}"
            f" {name}"
        )
    print()


def report_tsv(replacements: list[tuple[int, int, str]]) -> None:
    print("old_id\tnew_id\told_uri\tnew_uri\tnew_name")
    for old_id, new_id, name in replacements:
        old_uri = f"{OBO_URI_BASE}{old_id}"
        new_uri = f"{OBO_URI_BASE}{new_id}"
        print(f"NCBITaxon_{old_id}\tNCBITaxon_{new_id}\t{old_uri}\t{new_uri}\t{name}")


# ---------------------------------------------------------------------------
# Interactive prompting
# ---------------------------------------------------------------------------

def _read_tty(prompt: str) -> str:
    """
    Read a line of input from the terminal even when stdout is redirected.
    Falls back to stdin if /dev/tty is unavailable (e.g. Windows).
    """
    sys.stderr.write(prompt)
    sys.stderr.flush()
    try:
        with open('/dev/tty', 'r') as tty:
            return tty.readline().strip().lower()
    except OSError:
        return sys.stdin.readline().strip().lower()


def prompt_approvals(replacements: list[tuple[int, int, str]],
                     api_key: str | None) -> list[tuple[int, int, str]]:
    """
    For each replacement, display a lookup table comparing the old and new
    taxon IDs, then ask the user to approve or skip the substitution.
    Returns the subset the user approved.  Pressing 'q' stops prompting and
    returns whatever has been approved so far.
    """
    approved = []
    total = len(replacements)
    print(file=sys.stderr)

    for n, (old_id, new_id, name) in enumerate(replacements, 1):
        print(
            f"[{n}/{total}]  NCBITaxon_{old_id}  →  NCBITaxon_{new_id}  ({name})",
            file=sys.stderr,
        )

        # Show lookup table for old and new IDs side-by-side
        _print_lookup_table([old_id, new_id], api_key)

        answer = _read_tty("  Update? [y/n/q] (n): ")

        if answer == 'q':
            print("  Stopping at user request.", file=sys.stderr)
            break
        if answer == 'y':
            approved.append((old_id, new_id, name))
            print("  → Approved.", file=sys.stderr)
        else:
            print("  → Skipped.", file=sys.stderr)

    return approved


# ---------------------------------------------------------------------------
# File updates — one writer per format
# ---------------------------------------------------------------------------

def _update_owl(path: Path, approved: dict[int, tuple[int, str]]) -> int:
    """
    OWL / OFN: replace every occurrence of 'NCBITaxon_<old>' with
    'NCBITaxon_<new>' across the entire file.
    """
    text = path.read_text(encoding='utf-8')
    count = 0
    for old_id, (new_id, _) in approved.items():
        old_token = f'NCBITaxon_{old_id}'
        new_token = f'NCBITaxon_{new_id}'
        occurrences = text.count(old_token)
        if occurrences:
            text  = text.replace(old_token, new_token)
            count += occurrences
    path.write_text(text, encoding='utf-8')
    return count


def _update_ontofox(path: Path, approved: dict[int, tuple[int, str]]) -> int:
    """
    OntoFox .txt: for each replaced ID, insert a '# REPLACED NCBITaxon_<old>'
    comment before the URL line and update the URL and trailing name comment.
    The old label (trailing comment on the existing line) is preserved in the
    REPLACED comment, e.g.: # REPLACED NCBITaxon_6657 # Pancrustacea
    """
    lines = path.read_text(encoding='utf-8').splitlines(keepends=True)
    new_lines = []
    count = 0

    for line in lines:
        content = line.strip()
        if not content.startswith('#'):
            m = _ONTOFOX_URL.match(content)
            if m:
                old_id = int(m.group(1))
                if old_id in approved:
                    new_id, new_name = approved[old_id]
                    eol = '\n' if line.endswith('\n') else ''
                    # Carry the existing trailing label comment (if any) into
                    # the REPLACED line so the old name is preserved there.
                    hash_pos = content.find(' # ')
                    old_label = (' # ' + content[hash_pos + 3:]) if hash_pos != -1 else ''
                    new_lines.append(f'# REPLACED NCBITaxon_{old_id}{old_label}{eol}')
                    new_lines.append(
                        f'{OBO_URI_BASE}{new_id} # "{new_name}"{eol}'
                    )
                    count += 1
                    continue
        new_lines.append(line)

    path.write_text(''.join(new_lines), encoding='utf-8')
    return count


def _update_tsv(path: Path, approved: dict[int, tuple[int, str]]) -> int:
    """
    ROBOT template .tsv: replace 'NCBITaxon:<old>' with 'NCBITaxon:<new>'
    in column 0 of every matching row.
    """
    lines = path.read_text(encoding='utf-8').splitlines(keepends=True)
    new_lines = []
    count = 0

    for line in lines:
        col0 = line.split('\t', 1)[0].strip()
        m = _TSV_COL0.match(col0)
        if m:
            old_id = int(m.group(1))
            if old_id in approved:
                new_id, _ = approved[old_id]
                line  = line.replace(f'NCBITaxon:{old_id}', f'NCBITaxon:{new_id}', 1)
                count += 1
        new_lines.append(line)

    path.write_text(''.join(new_lines), encoding='utf-8')
    return count


def apply_updates(path: Path, fmt: str,
                  approved: list[tuple[int, int, str]]) -> int:
    """
    Dispatch file update to the format-appropriate writer.
    Returns the number of substitutions made in the file.
    """
    if not approved:
        return 0

    lookup = {old: (new, name) for old, new, name in approved}

    if fmt == 'OWL':
        return _update_owl(path, lookup)
    if fmt == 'OntoFox':
        return _update_ontofox(path, lookup)
    if fmt == 'TSV':
        return _update_tsv(path, lookup)
    return _update_owl(path, lookup)   # fallback for unknown formats


# ---------------------------------------------------------------------------
# OntoFox import helpers
# ---------------------------------------------------------------------------

def fetch_names(taxon_ids: list[int], api_key: str | None,
                batch_size: int) -> dict[int, str]:
    """
    Query NCBI for the current scientific name of each taxon ID.
    Returns {tax_id: name} for every ID the API recognises.
    """
    if not taxon_ids:
        return {}

    delay  = DELAY_WITH_KEY if api_key else DELAY_NO_KEY
    names: dict[int, str] = {}
    total  = len(taxon_ids)

    for start in range(0, total, batch_size):
        batch = taxon_ids[start:start + batch_size]
        try:
            entries = query_batch(batch, api_key, delay)
        except Exception as exc:
            print(f"\nWarning: name-fetch failed for batch starting at {batch[0]}: {exc}",
                  file=sys.stderr)
            continue
        for entry in entries:
            tax_id = entry.get('tax_id')
            name   = entry.get('current_scientific_name', {}).get('name', '')
            if tax_id and name:
                names[tax_id] = name
        if start + batch_size < total:
            time.sleep(delay)

    return names


def insert_into_ontofox(ontofox_path: Path, missing_ids: list[int],
                        names: dict[int, str], source_name: str) -> int:
    """
    Insert URL lines for missing_ids into the [Low level source term URIs]
    section of the OntoFox file, just before the
    [Top level source term URIs…] marker.  Returns the number of lines added.
    """
    if not missing_ids:
        return 0

    lines = ontofox_path.read_text(encoding='utf-8').splitlines(keepends=True)

    # Locate the insertion point
    insert_at = None
    for i, line in enumerate(lines):
        if line.strip() == ONTOFOX_INSERT_BEFORE:
            insert_at = i
            break

    new_block: list[str] = [f'# Added from {source_name}\n']
    for id_ in sorted(missing_ids):
        url  = f'{OBO_URI_BASE}{id_}'
        name = names.get(id_, '')
        new_block.append(f'{url} # "{name}"\n' if name else f'{url}\n')
    new_block.append('\n')   # blank separator line

    if insert_at is not None:
        # Ensure there is a blank line before the new block
        if insert_at > 0 and lines[insert_at - 1].strip():
            new_block.insert(0, '\n')
        lines[insert_at:insert_at] = new_block
    else:
        # Fallback: append at end of file
        if lines and lines[-1].strip():
            lines.append('\n')
        lines.extend(new_block)

    ontofox_path.write_text(''.join(lines), encoding='utf-8')
    return len(missing_ids)


# ---------------------------------------------------------------------------
# Name-comment sync
# ---------------------------------------------------------------------------

def sync_names_in_ontofox(path: Path, names: dict[int, str]) -> list[tuple[int, str, str]]:
    """
    Ensure every NCBITaxon URI line in an OntoFox file has a ``# "Current Scientific Name"``
    comment with the name in double quotes.

    Rules applied to each taxon URI line:
    - Quoted name present and correct:       no change.
    - Quoted name present but wrong:         replace quoted name, preserve any trailing text.
    - Unquoted comment or no comment:        replace entire comment with quoted NCBI name.

    Returns a list of ``(tax_id, old_comment_repr, new_comment_repr)`` for every changed
    line.  The file is written only if at least one line changes.
    """
    lines = path.read_text(encoding='utf-8').splitlines(keepends=True)
    new_lines: list[str] = []
    changes: list[tuple[int, str, str]] = []

    for line in lines:
        content = line.strip()
        # Preserve blank lines and comment-only lines unchanged
        if not content or content.startswith('#'):
            new_lines.append(line)
            continue

        m = _ONTOFOX_URL.match(content)
        if not m:
            new_lines.append(line)
            continue

        tax_id    = int(m.group(1))
        ncbi_name = names.get(tax_id)
        if ncbi_name is None:
            # ID not returned by NCBI (possibly invalid); leave unchanged
            new_lines.append(line)
            continue

        url_str   = m.group(0)                    # full URL text
        after_url = content[m.end():].strip()      # everything after the URL

        # Extract the comment text (strip the leading '# ')
        comment = after_url[1:].lstrip() if after_url.startswith('#') else ''

        # Determine whether the comment already starts with a quoted name
        qm = _QUOTED_NAME.match(comment)
        if qm:
            existing_name = qm.group(1)
            trailing      = qm.group(2).strip()
            if existing_name == ncbi_name:
                new_lines.append(line)
                continue
            # Quoted name is stale — update it, keep any trailing notes
            old_repr = f'"{existing_name}"' + (f' {trailing}' if trailing else '')
            new_repr = f'"{ncbi_name}"'     + (f' {trailing}' if trailing else '')
        else:
            # Unquoted comment (or no comment): replace entirely with quoted NCBI name
            old_repr = comment    # empty string when there was no comment
            new_repr = f'"{ncbi_name}"'

        eol = '\n' if line.endswith('\n') else ''
        new_lines.append(f'{url_str} # {new_repr}{eol}')
        changes.append((tax_id, old_repr, new_repr))

    if changes:
        path.write_text(''.join(new_lines), encoding='utf-8')

    return changes


# ---------------------------------------------------------------------------
# Taxon-column check (TSV 'taxon' heading → NCBI name lookup → ontofox update)
# ---------------------------------------------------------------------------

def extract_taxon_column_names(path: Path) -> list[str]:
    """
    Read a ROBOT template TSV and return sorted unique non-empty values from the
    column whose row-0 header is 'taxon' (case-insensitive).
    Row 1 (ROBOT directives) is skipped.  Returns [] if no 'taxon' column exists.
    """
    taxon_col: int | None = None
    names: set[str] = set()

    with path.open('r', encoding='utf-8') as fh:
        for row_idx, line in enumerate(fh):
            cols = line.rstrip('\n\r').split('\t')
            if row_idx == 0:
                for j, header in enumerate(cols):
                    if header.strip().lower() == 'taxon':
                        taxon_col = j
                        break
                if taxon_col is None:
                    return []
            elif row_idx == 1:
                continue  # ROBOT directive row
            else:
                if taxon_col < len(cols):
                    val = cols[taxon_col].strip()
                    # Strip OWL single-quote wrapping ('Name') if present
                    if val.startswith("'") and val.endswith("'") and len(val) > 2:
                        val = val[1:-1].strip()
                    # Split compound entries (e.g. "Taxon A|Taxon B" or "A;B")
                    for part in re.split(r'[;|]', val):
                        part = part.strip()
                        # Skip FoodOn plant-product names (not NCBITaxon entries)
                        if part and not part.lower().endswith(' plant'):
                            names.add(part)

    return sorted(names)


def build_ontofox_name_index(path: Path) -> dict[str, int]:
    """
    Build a ``{scientific_name: tax_id}`` index from an OntoFox file.
    Only lines with a ``# "quoted name"`` comment are indexed.
    """
    index: dict[str, int] = {}
    with path.open('r', encoding='utf-8') as fh:
        for line in fh:
            content = line.strip()
            if not content or content.startswith('#'):
                continue
            m = _ONTOFOX_URL.match(content)
            if not m:
                continue
            tax_id    = int(m.group(1))
            after_url = content[m.end():].strip()
            comment   = after_url[1:].lstrip() if after_url.startswith('#') else ''
            qm        = _QUOTED_NAME.match(comment)
            if qm:
                index[qm.group(1)] = tax_id
    return index


def _score_qualifier(qualifier: str, taxonomy: dict) -> int:
    """
    Count how many words from *qualifier* appear in the taxonomy's group name
    or classification rank names.  Used to pick the best match when NCBI
    returns multiple taxa with the same scientific name (homonyms).

    Example: qualifier ``"basidiomycete fungi"`` scores higher for a Fungi/
    Basidiomycota entry than for a Streptophyta entry.
    """
    qual_words = set(re.findall(r'[a-z]+', qualifier.lower()))
    parts: list[str] = [taxonomy.get('group_name', '') or '']
    for rank_data in (taxonomy.get('classification') or {}).values():
        if isinstance(rank_data, dict):
            parts.append(rank_data.get('name', '') or '')
    all_text = ' '.join(parts).lower()
    return sum(1 for w in qual_words if w in all_text)


def lookup_names_via_ncbi(
    names: list[str], api_key: str | None,
    batch_size: int = 100,
) -> dict[str, int | None]:
    """
    Query the NCBI datasets API for NCBITaxon IDs matching a list of scientific
    names, using the same ``/taxonomy/taxon/`` endpoint as the ID lookup (it
    accepts comma-separated scientific names as well as IDs).

    Names are sent in batches; each name is URL-encoded individually and joined
    with commas.  The ``query`` field in each response report maps results back
    to the original input name.

    NCBI disambiguation qualifiers of the form ``<qualifier>`` (e.g.
    ``Lactarius <basidiomycete fungi>``) are stripped before querying — NCBI
    uses these only in its web UI to distinguish homonymous names.  The result
    is mapped back to the original unstripped name.

    Returns ``{name: tax_id}`` for hits and ``{name: None}`` for names not
    found in NCBI or that caused an error.
    """
    results: dict[str, int | None] = {name: None for name in names}
    delay = DELAY_WITH_KEY if api_key else DELAY_NO_KEY

    # Strip "<qualifier>" disambiguation suffixes (NCBI web-UI only) and build
    # a map from the stripped search name back to original name(s).
    search_to_originals: dict[str, list[str]] = {}
    for name in names:
        search_name = re.sub(r'\s*<[^>]*>', '', name).strip()
        search_to_originals.setdefault(search_name, []).append(name)

    search_names = list(search_to_originals.keys())
    total     = len(search_names)
    n_batches = (total + batch_size - 1) // batch_size

    for batch_num, start in enumerate(range(0, total, batch_size), 1):
        batch = search_names[start:start + batch_size]
        pct   = 100 * batch_num // n_batches
        print(
            f"\r  NCBI name lookup [{pct:3d}%] batch {batch_num}/{n_batches} ...",
            end='', flush=True, file=sys.stderr,
        )
        # Encode each name individually (spaces → %20), keep commas as separators
        name_str = ','.join(urllib.parse.quote(n, safe='') for n in batch)
        url = f"{NCBI_API_BASE}/{name_str}/dataset_report?returned_content=METADATA"
        if api_key:
            url += f"&api_key={urllib.parse.quote(api_key)}"
        try:
            req = urllib.request.Request(url, headers={'accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())

            # Collect all (tax_id, taxonomy) candidates per query name first,
            # so homonyms can be disambiguated together.
            query_candidates: dict[str, list[tuple[int, dict]]] = {}
            for report in data.get('reports', []):
                taxonomy = report.get('taxonomy') or {}
                tax_id   = taxonomy.get('tax_id')
                if tax_id is None:
                    continue
                for query_name in report.get('query', []):
                    query_candidates.setdefault(query_name, []).append(
                        (tax_id, taxonomy)
                    )

            # Assign results to original names, using qualifier scoring when
            # NCBI returned multiple taxa for the same name (homonyms).
            for search_name, candidates in query_candidates.items():
                for orig_name in search_to_originals.get(search_name, []):
                    qual_m    = re.search(r'<([^>]*)>', orig_name)
                    qualifier = qual_m.group(1) if qual_m else None

                    if len(candidates) == 1 or qualifier is None:
                        results[orig_name] = candidates[0][0]
                    else:
                        # Score each candidate against the qualifier text and
                        # pick the best match.
                        best = max(
                            candidates,
                            key=lambda c: _score_qualifier(qualifier, c[1]),
                        )
                        results[orig_name] = best[0]
        except Exception as exc:
            print(
                f"\n  NCBI name lookup error (batch {batch_num}): {exc}",
                file=sys.stderr,
            )
        if batch_num < n_batches:
            time.sleep(delay)

    print(file=sys.stderr)  # newline after progress line
    return results


def check_taxon_column(
    tsv_path:     Path,
    ontofox_path: Path,
    update_mode:  str | None,
    api_key:      str | None,
) -> None:
    """
    If *tsv_path* has a 'taxon' column, verify that every unique scientific name in
    that column appears as a ``# "name"`` comment in *ontofox_path*.

    Missing names are reported.  When *update_mode* is active they are looked up in
    NCBI and, if found, added to *ontofox_path*:
    - ``'interactive'``: prompt [y/n] per entry.
    - ``'auto'``:        add all found entries without prompting.
    """
    taxon_names = extract_taxon_column_names(tsv_path)
    if not taxon_names:
        return

    print(
        f"\nChecking 'taxon' column: {len(taxon_names)} unique name(s) "
        f"against {ontofox_path} ...",
        file=sys.stderr,
    )

    if not ontofox_path.exists():
        print(
            f"  Warning: {ontofox_path} not found; skipping taxon-column check.",
            file=sys.stderr,
        )
        return

    name_index = build_ontofox_name_index(ontofox_path)
    missing    = [n for n in taxon_names if n not in name_index]

    if not missing:
        print(
            f"  All {len(taxon_names)} taxon name(s) are present in {ontofox_path}.",
            file=sys.stderr,
        )
        return

    print(f"  {len(missing)} taxon name(s) not found in {ontofox_path}:")
    for name in missing:
        print(f"    '{name}'")

    if update_mode is None:
        print(
            "\n  (Use --update or --update-auto to look up and add missing entries via NCBI.)",
            file=sys.stderr,
        )
        return

    # --- NCBI name lookup for missing entries ---
    print(
        f"\n  Looking up {len(missing)} name(s) via NCBI ...",
        file=sys.stderr,
    )
    ncbi_results = lookup_names_via_ncbi(missing, api_key)

    found   = {name: tid for name, tid in ncbi_results.items() if tid is not None}
    unfound = [name for name, tid in ncbi_results.items() if tid is None]

    if unfound:
        print(f"\n  Warning: {len(unfound)} name(s) not found in NCBI NCBITaxon:")
        for name in unfound:
            print(f"    '{name}'")

    if not found:
        return

    # --- Prompt or auto-approve ---
    if update_mode == 'interactive':
        approved: dict[str, int] = {}
        for name, tid in sorted(found.items()):
            answer = _read_tty(
                f"\n  Add NCBITaxon_{tid}  # \"{name}\"  to {ontofox_path}? [y/n] (y): "
            )
            if answer != 'n':
                approved[name] = tid
    else:  # auto
        approved = dict(found)
        print(
            f"  Auto-adding all {len(approved)} NCBI-found entry/entries ...",
            file=sys.stderr,
        )

    if not approved:
        print("  No entries added.", file=sys.stderr)
        return

    names_map = {tid: name for name, tid in approved.items()}
    n_added   = insert_into_ontofox(
        ontofox_path, sorted(names_map.keys()), names_map, tsv_path.name
    )
    print(f"\n  Added {n_added} entry/entries to {ontofox_path}:")
    for name, tid in sorted(approved.items()):
        print(f"    NCBITaxon_{tid}  # \"{name}\"")


# ---------------------------------------------------------------------------
# Missing-ID check (from ncbitaxon_check.py)
# ---------------------------------------------------------------------------

def run_missing_check() -> None:
    """
    Check whether every NCBITaxon ID listed in ncbitaxon_ontofox.txt is
    actually present in ncbitaxon_import.owl (and ncbitaxon2_import.owl if it
    exists).  Prints bracketed IDs for any that are absent.
    """
    content_dict: set[str] = set()

    for file_name in ['imports/ncbitaxon', 'imports/ncbitaxon2']:
        owl_path = file_name + '_import.owl'
        print()
        print("Checking for missing ids in", owl_path)
        try:
            with open(owl_path, 'r') as owl_handler:
                for term in owl_handler.read().splitlines():
                    term = term.strip()
                    if term.split('_')[0] == '<owl:Class rdf:about="http://purl.obolibrary.org/obo/NCBITaxon':
                        id = term.split('_')[1].split('"')[0]
                        content_dict.add(id)
        except FileNotFoundError:
            print(f"  (file not found — skipping)", file=sys.stderr)
            continue

        ontofox_path = file_name + '_ontofox.txt'
        try:
            with open(ontofox_path, 'r') as lookup_handler:
                for term in lookup_handler.read().splitlines():
                    if term.split('_')[0] == 'http://purl.obolibrary.org/obo/NCBITaxon':
                        id = term.split('_')[1].split(' ')[0].split('\t')[0]
                        if id not in content_dict:
                            print('[' + id + ']')
        except FileNotFoundError:
            print(f"  (ontofox file {ontofox_path} not found — skipping)", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Manage NCBITaxon IDs in FoodOn source files: detect replaced IDs, "
            "update files, ensure OntoFox import lists are complete, look up taxa, "
            "and check for import gaps."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage:")[1] if "Usage:" in __doc__ else "",
    )
    parser.add_argument(
        "input_file",
        nargs="?",
        default="imports/ncbitaxon_import.owl",
        help=(
            "File to scan for NCBITaxon IDs. "
            "Supported: .owl/.ofn (OWL), .txt (OntoFox), .tsv (ROBOT template). "
            "Default: imports/ncbitaxon_import.owl"
        ),
    )
    parser.add_argument(
        "--ids",
        metavar="ID_OR_NAME,...",
        help=(
            "Check only these comma-separated taxon IDs or scientific names "
            "(skips file parsing). Scientific names are resolved to NCBITaxon IDs "
            "via the NCBI API before the report is run."
        ),
    )

    # -l is mutually exclusive with --update, --update-auto, -i, and --sync-names
    parser.add_argument(
        "-l", "--lookup",
        metavar="IDs",
        help=(
            "Comma-delimited numeric NCBITaxon IDs to look up. "
            "Prints a markdown comparison table and exits. "
            "Cannot be combined with --update, --update-auto, -i, or --sync-names."
        ),
    )

    update_group = parser.add_mutually_exclusive_group()
    update_group.add_argument(
        "--update",
        action="store_true",
        default=False,
        help=(
            "Update the input file, prompting to approve each replacement one at a time. "
            "A lookup table is displayed for each candidate before prompting."
        ),
    )
    update_group.add_argument(
        "--update-auto",
        action="store_true",
        default=False,
        help="Update the input file, applying all replacements without prompting.",
    )
    parser.add_argument(
        "--sync-names",
        action="store_true",
        default=False,
        help=(
            "For OntoFox (.txt) input files: ensure every taxon URI line carries a "
            '# "Current Scientific Name" comment with the name in double quotes. '
            "Corrects stale quoted names and adds missing ones. "
            "Any text after the quoted name is preserved. "
            "Can be combined with --update."
        ),
    )
    parser.add_argument(
        "--api-key",
        metavar="KEY",
        help="NCBI API key (enables ~10 req/s instead of 3 req/s)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        metavar="N",
        help=(
            f"IDs per API request (default: {DEFAULT_BATCH_SIZE}). "
            "Stay at or below 500 to avoid HTTP 414 URL-too-long errors."
        ),
    )
    parser.add_argument(
        "--tsv",
        action="store_true",
        help="Output results as TSV instead of human-readable text",
    )
    parser.add_argument(
        "-i", "--import",
        dest="add_to_ontofox",
        action="store_true",
        default=False,
        help=(
            "After processing the input file, check whether each NCBITaxon ID "
            "(or its approved replacement) is present in imports/ncbitaxon_ontofox.txt "
            "and insert any that are missing. "
            "Ignored when the input file is already the OntoFox file."
        ),
    )
    parser.add_argument(
        "--check-missing",
        action="store_true",
        default=False,
        help=(
            "Check whether every ID in ncbitaxon_ontofox.txt is present in "
            "ncbitaxon_import.owl and print any that are absent. "
            "Runs instead of the normal replacement check."
        ),
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print per-batch progress details",
    )
    args = parser.parse_args()

    # --- -l/--lookup: show table and exit (incompatible with update/import) ---
    if args.lookup:
        blocked = [
            opt for opt, flag in [
                ('--update',      args.update),
                ('--update-auto', args.update_auto),
                ('-i/--import',   args.add_to_ontofox),
                ('--sync-names',  args.sync_names),
            ] if flag
        ]
        if blocked:
            parser.error(
                f"-l/--lookup cannot be combined with: {', '.join(blocked)}"
            )
        lookup_taxon(args.lookup, args.api_key)
        sys.exit(0)

    # --- --check-missing: legacy import-gap check ---
    if args.check_missing:
        run_missing_check()
        sys.exit(0)

    # --- Normal replacement-detection flow ---

    # Determine update mode
    if args.update:
        update_mode = 'interactive'
    elif args.update_auto:
        update_mode = 'auto'
    else:
        update_mode = None

    if update_mode and args.ids:
        print("Warning: --update/--update-auto has no effect when --ids is used (no file to update).",
              file=sys.stderr)
        update_mode = None

    if args.sync_names and args.ids:
        print("Warning: --sync-names has no effect when --ids is used (no file to update).",
              file=sys.stderr)

    # Collect IDs
    fmt = None
    input_path = None

    if args.ids:
        numeric_ids, name_tokens = parse_explicit_ids(args.ids)
        if name_tokens:
            print(
                f"Resolving {len(name_tokens)} scientific name(s) to NCBITaxon IDs ...",
                file=sys.stderr,
            )
            name_results = lookup_names_via_ncbi(
                name_tokens, api_key=args.api_key, batch_size=args.batch_size,
            )
            print(file=sys.stderr)
            for name, tid in name_results.items():
                if tid is not None:
                    print(f"  {name!r}  →  NCBITaxon_{tid}", file=sys.stderr)
                    numeric_ids.append(tid)
                else:
                    print(
                        f"  Warning: could not resolve {name!r} to a NCBITaxon ID",
                        file=sys.stderr,
                    )
            numeric_ids = sorted(set(numeric_ids))
        taxon_ids = numeric_ids
        print(f"Checking {len(taxon_ids)} explicitly specified ID(s) ...",
              file=sys.stderr)
    else:
        input_path = Path(args.input_file)
        if not input_path.exists():
            print(f"Error: file not found: {input_path}", file=sys.stderr)
            sys.exit(1)
        taxon_ids, fmt = extract_ids_from_file(input_path)
        print(
            f"Read {len(taxon_ids)} unique NCBITaxon IDs "
            f"from {input_path} [{fmt}].",
            file=sys.stderr,
        )

    if not taxon_ids:
        print("No NCBITaxon IDs found — nothing to check.", file=sys.stderr)
        sys.exit(0)

    # Query NCBI
    print(
        f"Querying NCBI API in batches of {args.batch_size} "
        f"({'with' if args.api_key else 'without'} API key) ...",
        file=sys.stderr,
    )
    replacements = find_replacements(
        taxon_ids,
        api_key=args.api_key,
        batch_size=args.batch_size,
        verbose=args.verbose,
    )

    # Report
    if args.tsv:
        report_tsv(replacements)
    else:
        report_human(replacements)

    # Update file
    approved: list[tuple[int, int, str]] = []

    if replacements and update_mode and input_path is not None:
        if update_mode == 'interactive':
            approved = prompt_approvals(replacements, args.api_key)
        else:  # auto
            approved = replacements
            print(
                f"Auto-applying all {len(approved)} replacement(s) ...",
                file=sys.stderr,
            )

        if not approved:
            print("No replacements applied.", file=sys.stderr)
        else:
            n_subs = apply_updates(input_path, fmt, approved)
            print(
                f"Updated {input_path}: {len(approved)} term(s) replaced "
                f"({n_subs} substitution(s) in file).",
                file=sys.stderr,
            )

    # Import missing IDs into ontofox
    if args.add_to_ontofox and input_path is not None:
        if fmt == 'OntoFox':
            print(
                "Note: -i/--import has no effect when the input file is "
                "already the OntoFox file.",
                file=sys.stderr,
            )
        elif not ONTOFOX_PATH.exists():
            print(f"Error: OntoFox file not found at {ONTOFOX_PATH}", file=sys.stderr)
        else:
            approved_map = {old: new for old, new, _ in approved}
            effective_ids = {approved_map.get(id_, id_) for id_ in taxon_ids}

            ontofox_ids = set(extract_ids_from_ontofox(ONTOFOX_PATH))
            missing = sorted(effective_ids - ontofox_ids)

            if not missing:
                print(
                    f"All {len(effective_ids)} ID(s) already present in {ONTOFOX_PATH}.",
                    file=sys.stderr,
                )
            else:
                print(
                    f"{len(missing)} ID(s) not in {ONTOFOX_PATH}; fetching names ...",
                    file=sys.stderr,
                )
                names = {new: name for _, new, name in replacements}
                unknown = [id_ for id_ in missing if id_ not in names]
                if unknown:
                    names.update(
                        fetch_names(unknown, api_key=args.api_key,
                                    batch_size=args.batch_size)
                    )
                n_added = insert_into_ontofox(
                    ONTOFOX_PATH, missing, names, input_path.name
                )
                print(
                    f"Added {n_added} term(s) to {ONTOFOX_PATH}.",
                    file=sys.stderr,
                )

    # --- Sync # "Scientific Name" comments in OntoFox file ---
    if args.sync_names and input_path is not None and not args.ids:
        if fmt != 'OntoFox':
            print(
                f"Warning: --sync-names only applies to OntoFox (.txt) files "
                f"(input is {fmt!r}); skipping.",
                file=sys.stderr,
            )
        else:
            print(
                f"\nFetching current scientific names for {len(taxon_ids)} ID(s) "
                f"to sync name comments ...",
                file=sys.stderr,
            )
            name_map = fetch_names(
                taxon_ids, api_key=args.api_key, batch_size=args.batch_size
            )
            name_changes = sync_names_in_ontofox(input_path, name_map)
            if name_changes:
                c_label = max(len(f'NCBITaxon_{t}') for t, _, _ in name_changes)
                print(
                    f"\nSynced name comments in {input_path}: "
                    f"{len(name_changes)} line(s) updated."
                )
                for tax_id, old_repr, new_repr in name_changes:
                    label    = f'NCBITaxon_{tax_id}'
                    old_disp = repr(old_repr) if old_repr else '(none)'
                    print(f"  {label:<{c_label}}  {old_disp}  →  {new_repr!r}")
            else:
                print(
                    f"All name comments in {input_path} are current.",
                    file=sys.stderr,
                )

    # --- Taxon-column check (TSV 'taxon' heading) ---
    if fmt == 'TSV' and input_path is not None and not args.ids:
        check_taxon_column(input_path, ONTOFOX_PATH, update_mode, args.api_key)


if __name__ == "__main__":
    main()
