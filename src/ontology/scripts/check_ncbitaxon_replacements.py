#!/usr/bin/env python3
"""
Check NCBITaxon IDs against the NCBI Taxonomy API to identify terms whose
IDs have been superseded by a new primary identifier, and optionally update
the source file with the replacements found.

A replacement is detected when a queried ID appears in the 'secondary_tax_ids'
list of an API response entry — meaning that ID is now a deprecated alias for
a different primary tax_id.

Supported input file formats (auto-detected by extension):
  .owl / .ofn   OWL ontology file — scans every line for 'NCBITaxon_<digits>'
  .txt          OntoFox input file — reads lines whose first token is a URL
                  containing 'NCBITaxon_<digits>' (comment lines skipped)
  .tsv          ROBOT template — reads 'NCBITaxon:<digits>' from column 0

Usage:
    python check_ncbitaxon_replacements.py [options] [input_file]

    input_file defaults to: imports/ncbitaxon_import.owl

Examples:
    # Check the default OWL import (report only):
    python scripts/check_ncbitaxon_replacements.py

    # Check an OntoFox source list and prompt to update each finding:
    python scripts/check_ncbitaxon_replacements.py --update imports/ncbitaxon_ontofox.txt

    # Check a ROBOT template and apply all replacements automatically:
    python scripts/check_ncbitaxon_replacements.py --update-auto ../templates/robot_seafood.tsv

    # TSV output (clean for piping or saving):
    python scripts/check_ncbitaxon_replacements.py --tsv imports/ncbitaxon_import.owl

    # Use an NCBI API key (10 req/s instead of 3 req/s):
    python scripts/check_ncbitaxon_replacements.py --api-key YOUR_KEY_HERE

    # Check specific IDs only:
    python scripts/check_ncbitaxon_replacements.py --ids 1913631,121025,492052

    # Check a TSV template and ensure all its IDs are in the OntoFox import list:
    python scripts/check_ncbitaxon_replacements.py -i ../templates/robot_seafood.tsv

    # Combine: update replacements AND import missing IDs into ontofox:
    python scripts/check_ncbitaxon_replacements.py --update -i ../templates/robot_seafood.tsv
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


# ---------------------------------------------------------------------------
# ID extraction — one parser per file format
# ---------------------------------------------------------------------------

_OWL_PATTERN = re.compile(r'NCBITaxon_(\d+)')
_TSV_COL0    = re.compile(r'^NCBITaxon:(\d+)')
_ONTOFOX_URL = re.compile(r'^https?://\S*NCBITaxon_(\d+)')


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


def parse_explicit_ids(id_string: str) -> list[int]:
    """Parse a comma-separated string of numeric taxon IDs."""
    ids = []
    for part in id_string.split(','):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
        elif part:
            print(f"Warning: ignoring non-numeric ID token: {part!r}",
                  file=sys.stderr)
    return sorted(set(ids))


# ---------------------------------------------------------------------------
# NCBI API
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


# ---------------------------------------------------------------------------
# Replacement detection
# ---------------------------------------------------------------------------

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
# Output
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


def prompt_approvals(replacements: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """
    For each replacement, show details and ask the user to approve or skip.
    Returns the subset the user approved.  Pressing 'q' stops prompting and
    returns whatever has been approved so far.
    """
    approved = []
    total = len(replacements)
    print(file=sys.stderr)

    for n, (old_id, new_id, name) in enumerate(replacements, 1):
        new_uri = f"{OBO_URI_BASE}{new_id}"
        print(
            f"[{n}/{total}]  NCBITaxon_{old_id}  →  NCBITaxon_{new_id}  ({name})",
            file=sys.stderr,
        )
        print(f"         {new_uri}", file=sys.stderr)

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
                        f'{OBO_URI_BASE}{new_id} # {new_name}{eol}'
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
        new_block.append(f'{url} # {name}\n' if name else f'{url}\n')
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
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Check NCBITaxon IDs in an ontology file against the NCBI Taxonomy "
            "API and report any that have been replaced by a new primary identifier. "
            "Optionally update the source file with the replacements found."
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
        metavar="ID1,ID2,...",
        help="Check only these comma-separated numeric taxon IDs (skips file parsing)",
    )
    update_group = parser.add_mutually_exclusive_group()
    update_group.add_argument(
        "--update",
        action="store_true",
        default=False,
        help="Update the input file, prompting to approve each replacement one at a time.",
    )
    update_group.add_argument(
        "--update-auto",
        action="store_true",
        default=False,
        help="Update the input file, applying all replacements without prompting.",
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
        "-v", "--verbose",
        action="store_true",
        help="Print per-batch progress details",
    )
    args = parser.parse_args()

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

    # --- Collect IDs ---
    fmt = None
    input_path = None

    if args.ids:
        taxon_ids = parse_explicit_ids(args.ids)
        print(f"Checking {len(taxon_ids)} explicitly specified IDs ...",
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

    # --- Query NCBI ---
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

    # --- Report ---
    if args.tsv:
        report_tsv(replacements)
    else:
        report_human(replacements)

    # --- Update file ---
    approved: list[tuple[int, int, str]] = []

    if replacements and update_mode and input_path is not None:
        if update_mode == 'interactive':
            approved = prompt_approvals(replacements)
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

    # --- Import missing IDs into ontofox ---
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
            # Compute effective IDs: use replacement ID where approved, else original
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
                # Seed name cache from already-known replacement names
                names = {new: name for _, new, name in replacements}
                # Fetch names for any IDs not already in the cache
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


if __name__ == "__main__":
    main()
