#!/usr/bin/env python3
"""
robot_deprecate.py

Deprecate ontology terms that carry obo:IAO_0100001 (replaced by) but are not
yet marked owl:deprecated.  Terms may be defined in the main ontology file or
in any of its imported component files.

Default (no --update): print a preview report of every change that would be
made, sectioned by source ontology file.  No files are modified.

With --update: apply the changes directly to the original files.  Review
with `git diff` afterwards.

Run from src/ontology/scripts/.
"""

import argparse
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────

OBO_NS = "http://purl.obolibrary.org/obo/"

SPARQL_PREFIXES = """\
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX oboInOwl: <http://www.geneontology.org/formats/oboInOwl#>
PREFIX obo: <http://purl.obolibrary.org/obo/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
"""

# Human-readable labels for common annotation property IRIs
PRED_DISPLAY = {
    "http://www.geneontology.org/formats/oboInOwl#hasSynonym":        "synonym",
    "http://www.geneontology.org/formats/oboInOwl#hasExactSynonym":   "exact synonym",
    "http://www.geneontology.org/formats/oboInOwl#hasRelatedSynonym": "related synonym",
    "http://www.geneontology.org/formats/oboInOwl#hasNarrowSynonym":  "narrow synonym",
    "http://www.geneontology.org/formats/oboInOwl#hasBroadSynonym":   "broad synonym",
    "http://purl.obolibrary.org/obo/IAO_0000118":                     "alternative label",
    "http://www.geneontology.org/formats/oboInOwl#hasDbXref":         "xref",
    "http://purl.obolibrary.org/obo/IAO_0000115":                     "definition",
    "http://purl.obolibrary.org/obo/IAO_0000119":                     "definition source",
    "http://www.w3.org/2000/01/rdf-schema#label":                     "label",
    "http://purl.obolibrary.org/obo/IAO_0000114":                     "curation status",
    "http://purl.obolibrary.org/obo/IAO_0000116":                     "editor note",
    "http://purl.obolibrary.org/obo/IAO_0100001":                     "replaced by",
    "http://www.w3.org/2002/07/owl#deprecated":                       "deprecated",
    "http://www.w3.org/2000/01/rdf-schema#comment":                   "comment",
    "http://www.w3.org/2000/01/rdf-schema#subClassOf":                "subClassOf",
}

# Annotation properties copied from deprecated term to replacement
TRANSFER_PRED_IRIS = {
    "http://www.geneontology.org/formats/oboInOwl#hasSynonym",
    "http://www.geneontology.org/formats/oboInOwl#hasExactSynonym",
    "http://www.geneontology.org/formats/oboInOwl#hasRelatedSynonym",
    "http://www.geneontology.org/formats/oboInOwl#hasNarrowSynonym",
    "http://www.geneontology.org/formats/oboInOwl#hasBroadSynonym",
    "http://purl.obolibrary.org/obo/IAO_0000118",
    "http://www.geneontology.org/formats/oboInOwl#hasDbXref",
    "http://purl.obolibrary.org/obo/IAO_0000115",
}

# Source files that cannot be written to directly — when the deprecated term
# lives in one of these files, annotation transfers to the replacement are
# redirected to the main input ontology (foodon-edit.ofn) instead.
READ_ONLY_COMPONENTS: set[str] = {"cdno_import.ofn", "food_materials.owl"}

# SPARQL prefixed names for transfer predicates (used in VALUES clause)
TRANSFER_PRED_SPARQL = (
    "oboInOwl:hasSynonym oboInOwl:hasExactSynonym "
    "oboInOwl:hasRelatedSynonym oboInOwl:hasNarrowSynonym oboInOwl:hasBroadSynonym "
    "obo:IAO_0000118 oboInOwl:hasDbXref obo:IAO_0000115"
)


# ── IRI / SPARQL helpers ─────────────────────────────────────────────────────

def iri_to_prefixed(iri: str) -> str:
    """Convert full IRI to obo: prefixed form, or <IRI> if not in obo namespace."""
    if iri.startswith(OBO_NS):
        return "obo:" + iri[len(OBO_NS):]
    return f"<{iri}>"


def pred_label(iri: str) -> str:
    """Return a human-readable label for a predicate IRI."""
    return PRED_DISPLAY.get(iri, iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1])


def sparql_to_iri(field: str) -> str:
    """Strip SPARQL angle brackets from an IRI field: <http://...> → http://..."""
    f = field.strip()
    return f[1:-1] if f.startswith("<") and f.endswith(">") else f


def sparql_to_display(field: str) -> str:
    """Convert a SPARQL result field (IRI or literal) to a readable string."""
    f = field.strip()
    if f.startswith("<") and f.endswith(">"):
        return f[1:-1]
    m = re.match(r'^"(.*)"(?:@[a-zA-Z-]+|\^\^.*)?$', f, re.DOTALL)
    return m.group(1) if m else f


def build_prefix_filter(prefixes: list[str]) -> str:
    """Build a SPARQL FILTER clause matching one or more IRI prefixes."""
    parts = []
    for p in prefixes:
        full = p if p.startswith("http") else OBO_NS + p
        parts.append(f'STRSTARTS(STR(?x), "{full}")')
    return "FILTER (" + " || ".join(parts) + ")"


def base_where(prefix_filter: str) -> str:
    """Return the shared WHERE clause body for all deprecation queries."""
    return f"""
WHERE {{
    ?x obo:IAO_0100001 ?replacement .
    FILTER (NOT EXISTS {{?x owl:deprecated true}})
    FILTER (NOT EXISTS {{?replacement owl:deprecated true}})
    {prefix_filter}
    ?x rdfs:label ?label .
"""


# ── Catalog / file helpers ───────────────────────────────────────────────────

def parse_catalog(catalog_path: str) -> dict[str, str]:
    """Parse catalog-v001.xml → {uri: absolute_local_path}."""
    catalog_dir = Path(catalog_path).parent
    ns = "urn:oasis:names:tc:entity:xmlns:xml:catalog"
    result: dict[str, str] = {}
    try:
        for elem in ET.parse(catalog_path).getroot().iter(f"{{{ns}}}uri"):
            uri   = elem.get("name", "")
            local = str((catalog_dir / elem.get("uri", "")).resolve())
            result[uri] = local
    except Exception as e:
        print(f"Warning: cannot parse catalog {catalog_path}: {e}", file=sys.stderr)
    return result


def parse_owl_imports(ofn_file: str) -> list[str]:
    """Return the list of Import() URIs declared in an OFN/OWL file."""
    text = Path(ofn_file).read_text(encoding="utf-8", errors="replace")
    return re.findall(r'Import\(<([^>]+)>\)', text)


def find_declaration_file(iri: str, candidates: list[str],
                          input_file: str | None = None) -> str | None:
    """
    Find which candidate OFN/OWL file declares this class.

    Primary search: Declaration(Class(<iri>)) in each candidate (in order,
    so foodon-edit.ofn is checked first).

    Fallback: if no Declaration exists anywhere, return input_file (the main
    edit file).  The old mention-count heuristic was unreliable — a term
    present only as a subClassOf filler in food_materials.owl would win even
    though the term's annotations live in foodon-edit.ofn.
    """
    prefixed = iri_to_prefixed(iri)
    patterns = (f"Declaration(Class({prefixed}))", f"Declaration(Class(<{iri}>))")
    for f in candidates:
        path = Path(f)
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        if any(p in content for p in patterns):
            return f
    # No Declaration found anywhere — default to the main input file.
    return input_file if input_file else (candidates[0] if candidates else None)


def rel(path: str | None, base: Path) -> str:
    """Return a short relative path for display, or '(not found)' if None."""
    if not path:
        return "(not found)"
    try:
        return str(Path(path).relative_to(base))
    except ValueError:
        return path


def transfer_target(src_file: str | None, input_file: str) -> tuple[str, bool]:
    """
    Return (target_file, was_redirected).
    Annotation transfers go to the deprecated term's own source file (src_file).
    If that file is a read-only component (e.g. food_materials.owl), redirect
    to input_file (foodon-edit.ofn) instead.
    """
    if src_file and Path(src_file).name in READ_ONLY_COMPONENTS:
        return input_file, True
    return (src_file or input_file), bool(not src_file)


# ── SPARQL execution helpers ─────────────────────────────────────────────────


def run_merge_query(input_file: str, query: str, outfile: str,
                    sparql_file: str = "robot_deprecate.sparql",
                    prefixes_json: str = "../config/context.json") -> list[list[str]]:
    """
    Write query to sparql_file, then run:
      robot merge --input input_file query --query sparql_file --output outfile
    The merged ontology is kept in memory; no intermediate OWL file is written.
    Returns TSV rows as field lists (header row skipped).
    """
    with open(sparql_file, "w") as f:
        f.write(query)
    subprocess.run(
        f"robot --add-prefixes {prefixes_json} --xml-entities "
        f"merge --input {input_file} "
        f"query --query {sparql_file} {outfile}",
        shell=True, check=True
    )
    rows: list[list[str]] = []
    with open(outfile) as f:
        for line in f.readlines()[1:]:
            line = line.rstrip("\n")
            if line:
                rows.append(line.split("\t"))
    return rows


def build_values_update(update_type: str, rows: list[list[str]]) -> str:
    """
    Build a SPARQL INSERT/DELETE…WHERE VALUES… update from a list of TSV rows.
    Each row becomes one binding tuple.
    """
    bindings = "\n".join(
        "(" + " ".join(f.strip() if f.strip() else '""' for f in r) + ")"
        for r in rows
    )
    return (
        f"{update_type} {{?subject ?predicate ?object}}\n"
        f"WHERE {{ VALUES (?subject ?predicate ?object) {{\n{bindings}\n}} }}"
    )


def apply_update(target_file: str, sparql: str,
                 sparql_file: str, prefixes_json: str = "../config/context.json") -> None:
    """Write sparql to sparql_file and apply it to target_file in-place."""
    with open(sparql_file, "w") as f:
        f.write(sparql)
    subprocess.run(
        f"robot --add-prefixes {prefixes_json} --xml-entities query "
        f"-i {target_file} --update {sparql_file} -o {target_file}",
        shell=True, check=True
    )


# ── Declaration helper ───────────────────────────────────────────────────────

def ensure_declarations(x_iris: list[str], dep_file: str) -> int:
    """
    Insert any missing Declaration(Class(?x)) into deprecation_import.ofn
    immediately after the last existing Declaration(Class(…)) line.
    Returns the number of declarations added.
    """
    path = Path(dep_file)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    content = "".join(lines)
    added = [
        f"Declaration(Class({iri_to_prefixed(iri)}))"
        for iri in x_iris
        if f"Declaration(Class({iri_to_prefixed(iri)}))" not in content
    ]
    if added:
        last = max(
            (i for i, ln in enumerate(lines) if ln.strip().startswith("Declaration(Class(")),
            default=len(lines) - 2
        )
        for decl in reversed(added):
            lines.insert(last + 1, decl + "\n")
        path.write_text("".join(lines), encoding="utf-8")
    return len(added)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deprecate ontology terms carrying obo:IAO_0100001 (replaced by).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Run from src/ontology/scripts/.
Default: preview report only.  Use --update to apply changes.

Examples:
  python3 robot_deprecate.py
  python3 robot_deprecate.py -i ../foodon-edit.ofn -p FOODON_
  python3 robot_deprecate.py -i ../foodon-edit.ofn -p FOODON_ --update
"""
    )
    parser.add_argument("-i", "--input", default="../foodon-edit.ofn", metavar="FILE",
                        help="Main ontology file to search (default: ../foodon-edit.ofn)")
    parser.add_argument("-p", "--prefix", nargs="+", default=["FOODON_"], metavar="PREFIX",
                        help="IRI prefix(es) for candidate terms (default: FOODON_). "
                             "Bare prefixes are expanded to http://purl.obolibrary.org/obo/PREFIX.")
    parser.add_argument("--update", action="store_true",
                        help="Apply changes to source files (default: preview only)")
    parser.add_argument("--catalog", default=None, metavar="FILE",
                        help="OWL catalog XML (auto-detected next to --input if omitted)")
    parser.add_argument("--deprecation-file", default=None, metavar="FILE",
                        help="OFN file to receive deprecated records "
                             "(auto-detected from catalog if omitted)")
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Process at most N terms (useful for testing one change "
                             "at a time before committing to the full run)")
    args = parser.parse_args()

    input_file = str(Path(args.input).resolve())
    repo_root  = Path(input_file).parent.parent.parent  # for display only

    # ── Catalog & source files ─────────────────────────────────────────────
    catalog_path = args.catalog
    if not catalog_path:
        p = Path(input_file).parent / "catalog-v001.xml"
        if p.exists():
            catalog_path = str(p)

    catalog_map: dict[str, str] = parse_catalog(catalog_path) if catalog_path else {}

    # Source files = the input file + only those imports declared via Import(...)
    # within the input file and resolvable via the catalog.
    # Files in an "imports/" directory are 3rd-party ontologies and are excluded.
    seen: set[str] = {input_file}
    source_files: list[str] = [input_file]
    for uri in parse_owl_imports(input_file):
        local = catalog_map.get(uri)
        if not local:
            continue
        p = Path(local)
        if not p.exists() or p.suffix not in (".ofn", ".owl"):
            continue
        if "/imports/" in str(p) or str(p) in seen:
            continue   # skip 3rd-party import files
        source_files.append(str(p))
        seen.add(str(p))

    # Deprecation import file (destination for deprecated records)
    dep_file = args.deprecation_file or next(
        (loc for loc in catalog_map.values() if "deprecation_import" in Path(loc).name),
        None
    )
    if not dep_file:
        d = Path(input_file).parent / "components" / "deprecation_import.ofn"
        if d.exists():
            dep_file = str(d)

    # Files to search for Declaration(Class(…)) — exclude the deprecation file itself
    # (it is the destination, not a source of live terms)
    decl_candidates = [f for f in source_files if f != dep_file]

    # ── SPARQL setup ──────────────────────────────────────────────────────
    prefix_filter = build_prefix_filter(args.prefix)
    base          = base_where(prefix_filter)

    print(f"Input:            {rel(input_file, repo_root)}")
    print(f"Prefix(es):       {', '.join(args.prefix)}")
    print(f"Deprecation file: {rel(dep_file, repo_root)}")
    mode_str = "UPDATE — changes will be written to source files" if args.update else "PREVIEW  (run with --update to apply)"
    print(f"Mode:             {mode_str}")
    print()

    # ── Discover terms + all annotations in one merged query ─────────────
    # Chains robot merge → query without writing any intermediate OWL file.
    # Terms already in deprecation_import.ofn carry owl:deprecated true and
    # are excluded automatically by the NOT EXISTS guards in base_where().
    # Query is written to robot_deprecate.sparql for inspection / reuse.
    print("Querying merged ontology ...")
    combined_rows = run_merge_query(
        input_file,
        SPARQL_PREFIXES
        + "SELECT DISTINCT ?x ?replacement ?pred ?val ?role\n"
        + base
        + '    {\n'
          '        ?x ?pred ?val . FILTER (!isBlank(?val))\n'
          '        BIND("term" AS ?role)\n'
          '    }\n'
          '    UNION\n'
          '    {\n'
          '        ?replacement ?pred ?val . FILTER (!isBlank(?val))\n'
          '        BIND("replacement" AS ?role)\n'
          '    }\n'
          '}',
        "temp_combined.tsv"
    )
    print()

    if not combined_rows:
        print("No terms queued for deprecation (no unapplied obo:IAO_0100001 annotations found).")
        return

    RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
    seen_pairs: dict[tuple[str, str], None] = {}   # ordered set of (x_iri, r_iri)
    anns_by_x: dict[str, list[tuple[str, str]]] = defaultdict(list)
    r_labels: dict[str, str] = {}
    term_labels: dict[str, str] = {}

    for r in combined_rows:
        if len(r) < 5:
            continue
        x_iri = sparql_to_iri(r[0])
        r_iri = sparql_to_iri(r[1])
        pred  = sparql_to_iri(r[2])
        val   = r[3].strip()
        role  = sparql_to_display(r[4])
        if not x_iri:
            continue
        seen_pairs[(x_iri, r_iri)] = None
        if role == "term":
            anns_by_x[x_iri].append((pred, val))
            if pred == RDFS_LABEL and x_iri not in term_labels:
                term_labels[x_iri] = sparql_to_display(val)
        elif role == "replacement":
            if pred == RDFS_LABEL and r_iri not in r_labels:
                r_labels[r_iri] = sparql_to_display(val)

    term_list: list[dict] = [
        {"x": x, "r": r, "label": term_labels.get(x, "")}
        for x, r in seen_pairs
    ]
    total_found = len(term_list)
    if args.limit:
        term_list = term_list[:args.limit]

    # ── Blank-node references from OTHER terms to the deprecated IRI ───────
    # Separate merge+query pass; written to temp_bn.sparql.
    bn_rows = run_merge_query(
        input_file,
        SPARQL_PREFIXES + "SELECT DISTINCT ?x ?subject ?predicate ?inner_predicate\n"
        + base
        + "    ?bn ?inner_predicate ?x . FILTER (isBlank(?bn))\n"
          "    ?subject ?predicate ?bn . FILTER (?subject != ?x)\n}",
        "temp_bn.tsv",
        sparql_file="temp_bn.sparql"
    )
    bns_by_x: dict[str, list[dict]] = defaultdict(list)
    for r in bn_rows:
        if len(r) >= 4:
            bns_by_x[sparql_to_iri(r[0])].append({
                "subject": sparql_to_iri(r[1]),
                "pred":    sparql_to_iri(r[2]),
                "inner":   sparql_to_iri(r[3]),
            })

    # ── Locate source file for each term and replacement ──────────────────
    for t in term_list:
        t["src"]  = find_declaration_file(t["x"], decl_candidates, input_file)
        t["rsrc"] = find_declaration_file(t["r"],  decl_candidates, input_file)

    # ── Preview report — sectioned by source ontology ─────────────────────
    by_src: dict[str, list[dict]] = defaultdict(list)
    for t in term_list:
        by_src[t["src"] or "(file not found)"].append(t)

    BAR = "─" * 64
    print("=" * 64)
    print("DEPRECATION PREVIEW")
    print("=" * 64)
    limit_note = f"  (limited to {args.limit} of {total_found})" if args.limit and args.limit < total_found else ""
    print(f"  {len(term_list)} term(s) queued for deprecation{limit_note}\n")

    for src_path in sorted(by_src):
        terms = by_src[src_path]
        print(f"Source ontology: {rel(src_path, repo_root)}  ({len(terms)} term(s))")
        print(BAR)

        for t in terms:
            x_iri  = t["x"]
            r_iri  = t["r"]
            label  = t["label"]
            rlabel = r_labels.get(r_iri, "")

            tgt_file, redirected = transfer_target(t["src"], input_file)

            src_label  = rel(t["src"], repo_root) if t["src"] else "(file not found)"
            src_rdonly = bool(t["src"] and Path(t["src"]).name in READ_ONLY_COMPONENTS)

            print(f"\n  DEPRECATE:  {iri_to_prefixed(x_iri)}")
            print(f'    label:    "{label}"')
            print(f"    file:     {src_label}"
                  + ("  [read-only — axioms will NOT be deleted by this script]"
                     if src_rdonly else ""))
            print(f"  REPLACE BY: {iri_to_prefixed(r_iri)}")
            print(f'    label:    "{rlabel}"')
            print(f"    file:     {rel(t['rsrc'], repo_root)}")

            anns = anns_by_x.get(x_iri, [])
            transfer = [(p, v) for p, v in anns if p in TRANSFER_PRED_IRIS]
            remove   = [(p, v) for p, v in anns if p not in TRANSFER_PRED_IRIS]

            if transfer:
                dest_label = rel(tgt_file, repo_root)
                redir_note = "  [food_materials.owl → redirected]" if redirected else ""
                print(f"  Annotations → copy onto {iri_to_prefixed(r_iri)} in {dest_label}{redir_note}:")
                for p, v in transfer:
                    print(f"    {pred_label(p)}: {sparql_to_display(v)[:80]}")
            if remove:
                skip_note = "  [read-only — will NOT be deleted]" if src_rdonly else ""
                print(f"  Annotations → remove from {src_label}{skip_note}:")
                for p, v in remove:
                    print(f"    {pred_label(p)}: {sparql_to_display(v)[:80]}")

            bns = bns_by_x.get(x_iri, [])
            if bns:
                print("  Blank-node references (will be repointed to replacement):")
                for bn in bns:
                    print(f"    {iri_to_prefixed(bn['subject'])}"
                          f" via {pred_label(bn['inner'])}")

            print(f"  → deprecation_import.ofn:")
            print(f'      Declaration(Class({iri_to_prefixed(x_iri)}))')
            print(f'      rdfs:label "obsolete: {label}"')
            print(f"      obo:IAO_0100001 {iri_to_prefixed(r_iri)}")
            print(f"      owl:deprecated true")

        print()

    total_bns = sum(len(v) for v in bns_by_x.values())
    if total_bns:
        print(f"Blank-node references to repoint: {total_bns} total")
    print()

    if not args.update:
        print("Run with --update to apply the changes above.")
        return

    # ══ UPDATE ═══════════════════════════════════════════════════════════════
    print("=" * 64)
    print("APPLYING CHANGES")
    print("=" * 64)

    # 1. Ensure Declaration(Class(?x)) in deprecation_import.ofn
    n = ensure_declarations([t["x"] for t in term_list], dep_file)
    print(f"  Declarations added to {Path(dep_file).name}: {n}")

    # 2. INSERT deprecation annotations → deprecation_import.ofn
    #    Rows are built from the in-memory term_list; no extra robot invocation needed.
    RDFS_LABEL_IRI = "http://www.w3.org/2000/01/rdf-schema#label"
    IAO_100001_IRI = "http://purl.obolibrary.org/obo/IAO_0100001"
    OWL_DEP_IRI    = "http://www.w3.org/2002/07/owl#deprecated"
    XSD_BOOL       = "http://www.w3.org/2001/XMLSchema#boolean"

    def _sparql_lit(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    obs_rows = [
        [f"<{t['x']}>", f"<{RDFS_LABEL_IRI}>", _sparql_lit(f"obsolete: {t['label']}")]
        for t in term_list if t["label"]
    ]
    rep_rows = [
        [f"<{t['x']}>", f"<{IAO_100001_IRI}>", f"<{t['r']}>"]
        for t in term_list
    ]
    dep_rows_ins = [
        [f"<{t['x']}>", f"<{OWL_DEP_IRI}>", f'"true"^^<{XSD_BOOL}>']
        for t in term_list
    ]
    for step_name, rows in [
        ("obsolete label", obs_rows),
        ("replaced-by",    rep_rows),
        ("owl:deprecated", dep_rows_ins),
    ]:
        if rows:
            apply_update(dep_file, build_values_update("INSERT", rows),
                         f"temp_insert_{step_name.replace(' ', '_')}.sparql")
            print(f"  INSERT {step_name}: {len(rows)} triples → {Path(dep_file).name}")

    # 3. DELETE all non-blank-node axioms about each deprecated term from its source file.
    #    Read-only files (food_materials.owl etc.) are skipped — those terms are
    #    dropped from their template source, not edited in place.
    src_of = {t["x"]: t["src"] for t in term_list}
    by_src_del: dict[str, list] = defaultdict(list)
    for x_iri, ann_list in anns_by_x.items():
        src = src_of.get(x_iri)
        if not src or Path(src).name in READ_ONLY_COMPONENTS:
            continue
        for pred, val in ann_list:
            by_src_del[src].append([f"<{x_iri}>", f"<{pred}>", val])

    for src_file, rows in by_src_del.items():
        apply_update(src_file, build_values_update("DELETE", rows),
                     f"temp_delete_{Path(src_file).stem}.sparql")
        print(f"  DELETE {len(rows)} axioms from {Path(src_file).name}")

    # 4. INSERT transferred annotations → deprecated term's own source file.
    #    food_materials.owl (read-only) is redirected to foodon-edit.ofn.
    by_rsrc_add: dict[str, list] = defaultdict(list)
    for t in term_list:
        transfer = [
            (pred, val) for pred, val in anns_by_x.get(t["x"], [])
            if pred in TRANSFER_PRED_IRIS
        ]
        if not transfer:
            continue
        raw_src = t["src"]
        tgt, redirected = transfer_target(raw_src, input_file)
        if redirected:
            print(f"  [redirect] annotation transfer for {t['r']}: "
                  f"{Path(raw_src).name if raw_src else '?'} → {Path(tgt).name}")
        for pred, val in transfer:
            by_rsrc_add[tgt].append([f"<{t['r']}>", f"<{pred}>", val])

    for tgt_file, rows in by_rsrc_add.items():
        apply_update(tgt_file, build_values_update("INSERT", rows),
                     f"temp_addann_{Path(tgt_file).stem}.sparql")
        print(f"  INSERT {len(rows)} annotation(s) to {Path(tgt_file).name}")

    # 5. Blank-node replacement: repoint ?x → ?replacement inside restrictions
    #    Uses VALUES with explicit pairs so no runtime owl:deprecated query is needed.
    bn_subject_files: set[str] = set()
    if bns_by_x:
        bn_pairs = "\n".join(
            f"    (<{t['x']}> <{t['r']}>)"
            for t in term_list if t["x"] in bns_by_x
        )
        bn_sparql = (
            SPARQL_PREFIXES
            + "DELETE { ?bn ?p ?x }\nINSERT { ?bn ?p ?replacement }\n"
              "WHERE {\n"
              f"    VALUES (?x ?replacement) {{\n{bn_pairs}\n    }}\n"
              "    ?bn ?p ?x .\n"
              "    FILTER (isBlank(?bn))\n"
              "}"
        )
        for x_iri, refs in bns_by_x.items():
            for ref in refs:
                sf = find_declaration_file(ref["subject"], decl_candidates, input_file)
                if sf:
                    bn_subject_files.add(sf)

        for sf in bn_subject_files:
            apply_update(sf, bn_sparql, "temp_bn_replace.sparql")
            print(f"  Blank-node references repointed in {Path(sf).name}")
    else:
        print("  No blank-node replacements needed.")

    # ── Summary ────────────────────────────────────────────────────────────
    changed = (
        ({dep_file} if dep_file else set())
        | set(by_src_del)
        | set(by_rsrc_add)
        | bn_subject_files
    )
    print()
    print("Done. Review changes:")
    for f in sorted(changed):
        print(f"  git diff {f}")


if __name__ == "__main__":
    main()
