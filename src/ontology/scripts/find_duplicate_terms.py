#!/usr/bin/env python3
"""
Checks a test run's mapping_table_output.tsv against the existing FoodOn
ontology to see if any of the new terms it would create already exist
under a different ID.

FoodOn builds its organism terms from separate templates, one per module:
plant, animal, seafood, fungi. Each one lives under its own folder,
src/ontology/imports/<module>/. All of them are built by the same script,
organism_parts.py, run through run_organism_parts.sh with MODULE=plant,
MODULE=animal, and so on; the module argument just tells that one script
which paths and module-specific rules to use. A test run of any module
always writes its own mapping_table_output.tsv into
src/ontology/imports/<module>/TEST_RUN_RESULTS/. That file, listing
every new term a test run would create, is what this script reads.

To run it, open a terminal in src/ontology/scripts/ (the same folder this
file is in) and run one of the commands under Usage below.

Usage:
  python3 find_duplicate_terms.py plant
  python3 find_duplicate_terms.py animal --input some_other_file.tsv

The second example is only for checking a specific saved file instead of
the current module's own TEST_RUN_RESULTS output, for example an older
test run kept under a different name. Most of the time, just the module
name is enough.

It always checks against ../foodon-full.owl by default, no matter which
module you're checking. That file is the single merged ontology with
everything in it, every module plus every import, so it's the right file
to check against regardless of whether you're running this for plant,
animal, seafood, or fungi. There's a --ontology flag to point it at a
different file instead, but that file has to be in the same merged,
plain RDF/XML form as foodon-full.owl.

Run this after a test run (MODE=test) and before a real run, if you want
an extra check on top of the curation flags.

What it checks:
  Tier 1: the new term's label matches an existing label exactly (after
          lowercasing and trimming extra spaces).
  Tier 2: the new term's label has the exact same set of words as an
          existing label, just in a different order or with different
          punctuation (for example "raw apple" vs "apple, raw").

Output:
  A TSV file next to the input, named possible_duplicate_matches.tsv,
  listing only the new terms that matched something. If nothing matched,
  the file is left with just its header row and no data, even if an
  older run had written real matches into it, so you're never looking
  at a stale list of duplicates that were already fixed.
"""

import argparse
import csv
import os
import re
import sys

LABEL_TAG_RE = re.compile(r'<rdfs:label[^>]*>(.*?)</rdfs:label>', re.DOTALL)

OUTPUT_FIELDNAMES = [
    'sheet_id', 'new_term_label', 'new_foodon_id',
    'matched_existing_label', 'matched_existing_id', 'match_type',
]

XML_UNESCAPE = {
    '&amp;': '&',
    '&lt;': '<',
    '&gt;': '>',
    '&quot;': '"',
    '&apos;': "'",
}


def unescape_xml(text):
    for entity, char in XML_UNESCAPE.items():
        text = text.replace(entity, char)
    return text


def normalize(label):
    """Lowercase, trim, and collapse repeated whitespace."""
    return re.sub(r'\s+', ' ', label.strip().lower())


def word_set(label):
    """Lowercase words only, punctuation dropped, order ignored."""
    words = re.findall(r'[a-z0-9]+', label.lower())
    return frozenset(words)


def read_ontology_text(ontology_file):
    with open(ontology_file, encoding='utf-8', errors='replace') as fh:
        return fh.read()


def load_existing_labels(text):
    """
    Reads rdfs:label values straight out of the ontology text.
    Returns two lookups keyed for Tier 1 and Tier 2 matching:
      by_normalized: {normalized label: original label}
      by_word_set:   {word set: original label}
    """
    by_normalized = {}
    by_word_set = {}

    for match in LABEL_TAG_RE.finditer(text):
        label = unescape_xml(match.group(1)).strip()
        if not label:
            continue
        by_normalized.setdefault(normalize(label), label)
        by_word_set.setdefault(word_set(label), label)

    return by_normalized, by_word_set


def find_id_for_label(text, label):
    """
    Given a label we already know exists, find the term ID it belongs to.
    Finds the label's own tag first, then looks backward for the nearest
    entity-opening tag before it (<owl:Class rdf:about="...">, or
    ObjectProperty/AnnotationProperty/NamedIndividual the same way) - that
    tag is the one this label actually belongs to.

    A plain "nearest rdf:about" isn't enough: a class that restricts to
    another class (an equivalence or subclass axiom with a taxon or part
    restriction) has its own inner rdf:about values for that reference,
    and those can sit between the real owning tag and the label, closer
    to it. Anchoring on the entity-opening tags specifically (owl:Class,
    not rdf:Description or other nested elements) skips over those.
    """
    escaped = re.escape(label)
    label_tag = re.compile(r'<rdfs:label[^>]*>' + escaped + r'</rdfs:label>')
    label_match = label_tag.search(text)
    if not label_match:
        return ''

    entity_open_re = re.compile(r'<owl:(?:Class|ObjectProperty|AnnotationProperty|NamedIndividual) rdf:about="([^"]+)"')
    last_uri = None
    for m in entity_open_re.finditer(text, 0, label_match.start()):
        last_uri = m.group(1)
    if not last_uri:
        return ''

    return last_uri.rsplit('/', 1)[-1].replace('_', ':')


def load_new_terms(input_tsv):
    """Reads the Sheet_ID / FOODON_Label / FOODON_ID columns from a run's output."""
    rows = []
    with open(input_tsv, newline='', encoding='utf-8') as fh:
        reader = csv.DictReader(fh, delimiter='\t')
        for row in reader:
            label = (row.get('FOODON_Label') or row.get('Sheet_Label') or '').strip()
            if not label:
                continue
            rows.append({
                'sheet_id': row.get('Sheet_ID', ''),
                'label': label,
                'new_id': row.get('FOODON_ID', ''),
            })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('module', help='plant, animal, seafood, or fungi')
    parser.add_argument('--input', help='path to mapping_table_output.tsv '
                         '(default: imports/<module>/TEST_RUN_RESULTS/mapping_table_output.tsv)')
    parser.add_argument('--ontology', default='../foodon-full.owl',
                         help='ontology file to check against (default: ../foodon-full.owl)')
    parser.add_argument('--output', help='where to write results '
                         '(default: possible_duplicate_matches.tsv next to the input file)')
    args = parser.parse_args()

    input_path = args.input or os.path.join(
        '..', 'imports', args.module, 'TEST_RUN_RESULTS', 'mapping_table_output.tsv')

    if not os.path.exists(input_path):
        print(f'Cannot find {input_path}', file=sys.stderr)
        print('Run a test run for this module first, or pass --input directly.', file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.ontology):
        print(f'Cannot find ontology file {args.ontology}', file=sys.stderr)
        sys.exit(1)

    output_path = args.output or os.path.join(os.path.dirname(input_path),
                                                'possible_duplicate_matches.tsv')

    new_terms = load_new_terms(input_path)
    print(f'{len(new_terms)} new term(s) in {input_path}')

    print(f'Reading existing labels from {args.ontology} ...')
    ontology_text = read_ontology_text(args.ontology)
    by_normalized, by_word_set = load_existing_labels(ontology_text)
    print(f'{len(by_normalized)} existing labels loaded.')

    matches = []
    for term in new_terms:
        normalized = normalize(term['label'])
        words = word_set(term['label'])

        matched_label = None
        match_type = None

        if normalized in by_normalized:
            matched_label = by_normalized[normalized]
            match_type = 'exact label'
        elif words in by_word_set:
            matched_label = by_word_set[words]
            match_type = 'same words, different order or punctuation'

        if matched_label:
            matched_id = find_id_for_label(ontology_text, matched_label)
            matches.append({
                'sheet_id': term['sheet_id'],
                'new_term_label': term['label'],
                'new_foodon_id': term['new_id'],
                'matched_existing_label': matched_label,
                'matched_existing_id': matched_id,
                'match_type': match_type,
            })

    if not matches:
        print('No possible duplicates found.')
        if os.path.exists(output_path):
            # Overwrite rather than delete: this script only ever needs
            # write access to its own output file, never delete access to
            # anything in the repo, and this still gets rid of stale
            # matches from an earlier run that have since been fixed.
            with open(output_path, 'w', newline='', encoding='utf-8') as fh:
                writer = csv.DictWriter(fh, fieldnames=OUTPUT_FIELDNAMES, delimiter='\t')
                writer.writeheader()
            print(f'Cleared the old matches out of {output_path} '
                  '(from a previous run) since none of them are duplicates anymore.')
        return

    with open(output_path, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=OUTPUT_FIELDNAMES, delimiter='\t')
        writer.writeheader()
        writer.writerows(matches)

    print(f'{len(matches)} possible duplicate(s) found, written to {output_path}')
    print('Check each one. If it is a real duplicate, use --merge-id-reuse in '
          'organism_parts.py to map it to the existing ID instead of creating a new one.')


if __name__ == '__main__':
    main()
