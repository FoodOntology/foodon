#!/usr/bin/env python3
"""
Author : Anoosha Sehar https://orcid.org/0000-0001-5275-8866
Date   : 2026-09-28
Author : Kai Blumberg https://orcid.org/0000-0002-3410-4655
Date   : 2022-11-28
Purpose: Generate an organism parts robot template from an input template file, species list and organism module


run: ./organism_parts.py -t input_templates/12.tsv -i species_list.tsv -o output_templates/test_output.tsv -m input_ontology/id_mapping_table.tsv -n results/mapping_table_output.tsv -org animal -id input_ontology/id_integer.txt -c '<term_contributor_orcid_1>|<term_contributor_orcid_2>'
"""

import argparse
import sys
import csv
import datetime
from collections import namedtuple
import re  # PLANT-ONLY import; unused by the original animal/seafood path.


# --------------------------------------------------
def get_args():
    """get command-line arguments"""
    parser = argparse.ArgumentParser(
        description='Argparse Python script',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument(
        '-t',
        '--template',
        help='input template tsv file',
        metavar='str',
        type=str,
        default='')

    parser.add_argument(
        '-i',
        '--input',
        help='input species list tsv file',
        metavar='str',
        type=str,
        default='')

    parser.add_argument(
        '-o',
        '--output',
        help='output file path',
        metavar='str',
        type=str,
        default='')

    parser.add_argument(
        '-m',
        '--mapping',
        help='input tsv file for ID mapping lookup table',
        metavar='str',
        type=str,
        default='')

    parser.add_argument(
        '-n',
        '--new_terms',
        help='output tsv file of new terms to add to ID mapping table',
        metavar='str',
        type=str,
        default='')

    parser.add_argument(
        '-org',
        '--organism',
        help='Module to run e.g., animal or plant',
        metavar='str',
        type=str,
        default='')

    parser.add_argument(
        '-id',
        '--identifier',
        help='File with latest ID integer for the module',
        metavar='str',
        type=str,
        default='')

    parser.add_argument(
        '-c',
        '--contributor',
        help='String of | delimited term contributor ORIDs without spaces e.g. "https://orcid-866|https://orcid-165"',
        metavar='str',
        type=str,
        default='')

    # PLANT-ONLY: menus.tsv, used only when -org plant. Harmless/unused otherwise.
    parser.add_argument(
        '-mu',
        '--menu',
        help='PLANT-ONLY: menus.tsv, maps each checked picklist item to its plant_parts.tsv row',
        metavar='str',
        type=str,
        default='')

    # PLANT-ONLY, off in test mode: local-only log recording the date (xsd:dateTime,
    # same UTC format as the 'created' column) each species was FIRST compiled.
    # Never fetched from/written to Google Sheets - append-only, existing species'
    # dates are never touched again once recorded. Optional: if left blank, this
    # feature is simply skipped (nothing breaks for animal/seafood/fungus).
    parser.add_argument(
        '-sa',
        '--species_added_log',
        help="PLANT-ONLY: local tsv logging each species' first-compiled date (xsd:dateTime, UTC)",
        metavar='str',
        type=str,
        default='')

    # PLANT-ONLY, off in test mode: local, git-tracked tsv recording one row per
    # REAL run - date, how many species were compiled, and how many total terms
    # (new + reused) came out of them. Append-only, meant to stay in the repo
    # and build up a running history over time (e.g. "how many terms has the
    # plant module produced in total since it started"). A test run never
    # touches this - same reasoning as species_added_log above, test runs are
    # disposable and shouldn't pollute a permanent history.
    parser.add_argument(
        '-ch',
        '--compile_history_log',
        help='PLANT-ONLY: local tsv logging one row per real run (date, species compiled, terms generated)',
        metavar='str',
        type=str,
        default='')

    # PLANT-ONLY, off by default: for demo/test runs, tags every brand-new term
    # with a visible note so it's easy to spot and remove in Protege before the
    # final compile. Leave this off for the real final run so nothing gets
    # tagged. Existing (already-assigned) terms are never tagged either way.
    parser.add_argument(
        '-tn',
        '--tag_new_terms',
        help='PLANT-ONLY: tag newly-minted terms with a removable "demo/test term" note',
        action='store_true')

    # PLANT-ONLY, standalone mode: append manual ID-reuse rows to id_mapping_table.tsv
    # from a plain staging file (columns: species, menu_name, title,
    # existing_FOODON_ID, notes). When this is set, the script does ONLY this and
    # exits - no compile happens. Only ever appends, never rewrites/deletes a row.
    parser.add_argument(
        '--merge-id-reuse',
        dest='merge_id_reuse',
        help='PLANT-ONLY: path to a staging tsv of manual ID-reuse rows to append to -m. Runs this instead of a compile.',
        metavar='str',
        type=str,
        default='')

    # Used only by --merge-id-reuse: preview what would happen without writing.
    parser.add_argument(
        '--dry-run',
        dest='dry_run',
        help='With --merge-id-reuse: show what would be appended without writing anything',
        action='store_true')

    # Used only by --merge-id-reuse: where to look up the real label of the
    # existing_FOODON_ID being reused, so FOODON_Label isn't left blank.
    parser.add_argument(
        '--ontology',
        dest='ontology',
        help='With --merge-id-reuse: merged ontology file to look up existing labels from',
        metavar='str',
        type=str,
        default='../foodon-full.owl')

    return parser.parse_args()


# --------------------------------------------------
def warn(msg):
    """Print a message to STDERR"""
    print(msg, file=sys.stderr)


# --------------------------------------------------
def die(msg='Something bad happened'):
    """warn() and exit with error"""
    warn(msg)
    sys.exit(1)


# --------------------------------------------------
# PLANT-ONLY: two automatic curation checks, always run at the end of every
# plant compile (both MODE=test and MODE=run) - folded in from the standalone
# flag_genus_level_taxa.py / flag_term_overlaps.py scripts so nobody has to
# remember to run them separately. Read-only: neither one changes any file,
# they only add to plant_curation_flags.txt.
def check_genus_level_taxa(species_list):
    """Flags any row whose 'taxon' is a bare genus name (e.g. "Rubus") and whose
    'logic' column is still blank - meaning nobody has recorded a decision yet
    about whether this row should equal that genus (a generic/umbrella term,
    e.g. "citrus fruit" = Citrus - set 'logic' = 'only equivalency') or should
    only be A KIND OF that genus (a specific item that would otherwise collide
    with the umbrella term's own equivalence axiom - set 'logic' = 'only
    subClassOf'). Once either value is recorded, the row is treated as
    reviewed and won't be flagged again."""
    from collections import defaultdict
    by_ncbitaxon = defaultdict(list)
    for r in species_list:
        if r.get('Compiled', '').strip() != 'TRUE':
            continue  # not live yet - don't let it count as a "shared" sibling either
        ncbi = r.get('ID', '').strip()
        if ncbi:
            by_ncbitaxon[ncbi].append(r)

    flags = []
    for r in species_list:
        if r.get('Compiled', '').strip() != 'TRUE':
            continue  # only flag rows that are actually live/generated right now
        species = r.get('species', '').strip()
        taxon_text = r.get('taxon', '').strip()
        ncbi = r.get('ID', '').strip()
        if not species or not taxon_text or not ncbi:
            continue
        if ' ' in taxon_text:
            continue  # has a space - not a bare genus name
        # If a curator already reviewed this row and recorded a decision in plants.tsv's
        # 'logic' column - this is a live switch the compile actually reads, see the
        # axiom-generation code below - there's nothing left to flag here. Either value
        # counts as "reviewed": 'only subclassOf' means they decided this item is A KIND
        # OF the genus, not equal to it; 'only equivalency' means they decided it's
        # intentionally meant to equal the genus (e.g. a generic umbrella term like
        # "citrus fruit" that should automatically cover every species under it).
        logic_now = r.get('logic', '').strip().lower()
        if logic_now in ('only subclassof', 'only equivalency'):
            continue
        siblings = [x for x in by_ncbitaxon.get(ncbi, []) if x.get('species', '').strip() != species]
        if siblings:
            sib_names = ', '.join(x.get('species', '').strip() for x in siblings)
            flags.append(
                f"[GENUS-LEVEL, SHARED] species={species!r}: taxon={taxon_text!r} ({ncbi}) is a bare genus name, "
                f"and is ALSO used by: {sib_names}. Right now this gets the default equivalence axiom (claims this "
                f"row IS that genus). Needs a curator/Damion decision: if {species!r} is meant to be the generic "
                f"umbrella term for this genus, set 'logic' = 'only equivalency' to mark it reviewed; if it's "
                f"meant to be a specific item within the genus, set 'logic' = 'only subClassOf' instead."
            )
        else:
            flags.append(
                f"[GENUS-LEVEL] species={species!r}: taxon={taxon_text!r} ({ncbi}) is a bare genus name (not "
                f"shared with another species right now, but still not a species-level match, and 'logic' is "
                f"still blank). Review and set 'logic' to either 'only equivalency' (intentionally equal to the "
                f"genus) or 'only subClassOf' (a kind of the genus) to mark this reviewed."
            )
    return flags


def check_term_overlaps(species_list, template_list, menu_mapping_list, mapping_list):
    """Flags the 'onion vs onion bulb' situation: a species picking up BOTH
    the generic bare-organism term AND a specific part-template whose
    part-word matches that species' own anatomical_parent text - the two
    likely describe the same real-world part and may need to be merged."""
    MENU_COLS = ('PlantMaterialMenu', 'PlantFruitMenu', 'PlantSeedMenu',
                 'PlantLeafMenu', 'ShootSystemMenu', 'RootSystemMenu')
    comma_in_parens = re.compile(r"(\(\w*\s*\w*\b)(,)(\w*\s*\w*\b\))")

    def split_selections(val):
        result = re.sub(comma_in_parens, r'\1\3', val)
        return [x.replace('"', '').strip() for x in result.split(', ') if x.strip()]

    def strip_organism_placeholder(label_template):
        if not label_template:
            return None
        t = label_template.strip()
        for ph in ('{organism_base}', '{organism}'):
            if t.startswith(ph):
                rest = t[len(ph):].strip()
                return rest if rest else None
        return None

    template_by_id = {}
    for r in template_list:
        template_by_id.setdefault(r.get('ID', '').strip(), r)

    # Sheet_ID -> row, so we can tell whether two terms were already merged onto the
    # same real FOODON_ID (e.g. via --merge-id-reuse) - if so, they're resolved and
    # shouldn't keep getting flagged.
    mapping_by_sheet_id = {r.get('Sheet_ID', '').strip(): r for r in mapping_list if r.get('Sheet_ID', '').strip()}

    def already_merged(sheet_id_a, sheet_id_b):
        entry_a = mapping_by_sheet_id.get(sheet_id_a)
        entry_b = mapping_by_sheet_id.get(sheet_id_b)
        if not entry_a or not entry_b:
            return False
        id_a = entry_a.get('FOODON_ID', '').strip()
        id_b = entry_b.get('FOODON_ID', '').strip()
        return bool(id_a) and id_a == id_b

    flags = []
    for s in species_list:
        if s.get('Compiled', '').strip() != 'TRUE':
            continue  # only flag rows that are actually live/generated right now
        species_name = s.get('species', '').strip()
        if not species_name:
            continue
        raw_ap = s.get('anatomical_parent', '').strip()
        ap_id = s.get('anatomical_parent_id', '').strip()

        selections = []
        for menu_name in MENU_COLS:
            val = s.get(menu_name, '').strip()
            if not val:
                continue
            for title in split_selections(val):
                selections.append((menu_name, title))

        if not selections or not raw_ap or not ap_id:
            continue

        has_generic_selection = False
        specific_part_words = []
        for menu_name, title in selections:
            cand = [d for d in menu_mapping_list if d.get('title', '') == title and d.get('name', '') == menu_name]
            if not cand:
                continue
            template_id = cand[0].get('meaning', '').strip()
            if not template_id:
                continue
            if '{anatomical_parent}' in template_id:
                has_generic_selection = True
                continue
            row = template_by_id.get(template_id)
            if not row:
                continue
            part_word = strip_organism_placeholder(row.get('label', ''))
            if part_word:
                specific_part_words.append((menu_name, title, part_word.lower(), row.get('ID', '')))

        ap_norm = raw_ap.lower().strip()
        generic_sheet_id = f"{raw_ap}-{species_name}"
        if has_generic_selection:
            for menu_name, title, part_word, tid in specific_part_words:
                if part_word == ap_norm:
                    specific_sheet_id = f"{tid}-{species_name}"
                    if already_merged(generic_sheet_id, specific_sheet_id):
                        continue  # already resolved - both point at the same real ID now
                    flags.append(
                        f"[POSSIBLE OVERLAP] species={species_name!r}: anatomical_parent={raw_ap!r} matches the "
                        f"part-word generated by {menu_name} -> {title!r} (plant_parts ID {tid}), and the generic "
                        f"bare-organism term is also selected for this species. These likely describe the same "
                        f"real-world part (same situation as onion / onion bulb). Needs a curator decision: keep "
                        f"only the specific term, or add a manual id_mapping_table.tsv row (--merge-id-reuse) so "
                        f"the specific term reuses the generic term's ID."
                    )

        seen = {}
        for menu_name, title, part_word, tid in specific_part_words:
            if part_word in seen and seen[part_word][2] != tid:
                other_menu, other_title, _, other_tid = seen[part_word]
                other_sheet_id = f"{other_tid}-{species_name}"
                this_sheet_id = f"{tid}-{species_name}"
                if already_merged(other_sheet_id, this_sheet_id):
                    continue  # already resolved - both point at the same real ID now
                flags.append(
                    f"[POSSIBLE DUPLICATE SELECTION] species={species_name!r}: both {other_menu} -> {other_title!r} "
                    f"({other_tid}) and {menu_name} -> {title!r} ({tid}) generate the same part-word {part_word!r} "
                    f"- check whether both are really needed."
                )
            else:
                seen[part_word] = (menu_name, title, tid, tid)
    return flags


_XML_UNESCAPE = {'&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&apos;': "'"}


def _unescape_xml(text):
    for entity, char in _XML_UNESCAPE.items():
        text = text.replace(entity, char)
    return text


def find_label_for_id(ontology_text, curie):
    """
    Given a CURIE like 'FOODON:03413400', finds that term's real rdfs:label
    in the already-read ontology text. Used by --merge-id-reuse so
    FOODON_Label reflects the existing term's actual label, which isn't
    always the same text as the new Sheet_Label it's being matched against
    (e.g. "jack bean plant" reusing the ID for "jack-bean plant").

    Returns '' if the ID isn't found, or if no label turns up before the
    next entity starts (so a wrong/missing ID doesn't silently grab some
    unrelated label further down the file).

    Anchored specifically on the entity's own opening declaration tag
    (<owl:Class rdf:about="...">, or ObjectProperty/AnnotationProperty/
    NamedIndividual the same way) rather than a bare rdf:about="...". A
    plain rdf:about="..." can also appear as a NESTED reference inside
    some other entity's equivalence/restriction axiom (e.g.
    <rdf:Description rdf:about="...">), which can sit earlier in the file
    than the real declaration and get matched first - anchoring on the
    entity-opening tags specifically skips over those.
    """
    suffix = curie.replace(':', '_')
    about_re = re.compile(r'<owl:(?:Class|ObjectProperty|AnnotationProperty|NamedIndividual) '
                           r'rdf:about="[^"]*/' + re.escape(suffix) + r'">')
    about_match = about_re.search(ontology_text)
    if not about_match:
        return ''

    search_start = about_match.end()
    next_entity_re = re.compile(r'<owl:(?:Class|ObjectProperty|AnnotationProperty|NamedIndividual) rdf:about="')
    next_entity_match = next_entity_re.search(ontology_text, search_start)
    boundary = next_entity_match.start() if next_entity_match else len(ontology_text)

    label_re = re.compile(r'<rdfs:label[^>]*>(.*?)</rdfs:label>', re.DOTALL)
    label_match = label_re.search(ontology_text, search_start, boundary)
    if not label_match:
        return ''
    return _unescape_xml(label_match.group(1)).strip()


# --------------------------------------------------
# PLANT-ONLY, standalone mode (--merge-id-reuse): append manual ID-reuse rows
# to id_mapping_table.tsv from a plain staging file, so a curator never has to
# hand-compute a Sheet_ID. Only ever APPENDS - never rewrites or deletes an
# existing row. Re-running is always safe: anything already present is
# skipped, not duplicated. Folded in from the standalone merge_manual_id_reuse.py.
def run_merge_id_reuse(args):
    def load_tsv(path):
        with open(path, newline='', encoding='utf-8') as f:
            return list(csv.DictReader(f, delimiter='\t'))

    template_list_local = load_tsv(args.template)
    template_by_id = {r.get('ID', '').strip(): r for r in template_list_local}

    species_list_local = load_tsv(args.input)
    # Build species-name -> row lookup. plants.tsv can have more than one row
    # sharing the same species name (the compiled, active row plus a blank
    # not-yet-started placeholder further down in the backlog) - a plain dict
    # comprehension would let whichever row comes last in the file silently
    # win, even if it's the blank one. Prefer a Compiled == 'TRUE' row when
    # there's a choice, so --merge-id-reuse always resolves against the real,
    # live species data.
    species_by_name = {}
    for s in species_list_local:
        name = s.get('species', '').strip()
        if not name:
            continue
        existing = species_by_name.get(name)
        if existing is None:
            species_by_name[name] = s
        elif existing.get('Compiled', '').strip() != 'TRUE' and s.get('Compiled', '').strip() == 'TRUE':
            species_by_name[name] = s

    menu_list = load_tsv(args.menu)

    with open(args.mapping, newline='', encoding='utf-8') as f:
        mapping_header = f.readline().rstrip('\n').split('\t')
    existing_rows = load_tsv(args.mapping)
    existing_by_sheet_id = {r.get('Sheet_ID', '').strip(): r for r in existing_rows if r.get('Sheet_ID', '').strip()}

    staging_rows = load_tsv(args.merge_id_reuse)

    ontology_text = ''
    try:
        with open(args.ontology, encoding='utf-8', errors='replace') as f:
            ontology_text = f.read()
    except OSError as e:
        warn(f"Could not read {args.ontology} ({e}) - FOODON_Label will be left "
             f"blank for any row added this run. Pass --ontology to point at the "
             f"right file, then re-run to backfill it.")

    to_append = []
    report = []

    for n, row in enumerate(staging_rows, start=1):
        species_name = row.get('species', '').strip()
        menu_name = row.get('menu_name', '').strip()
        title = row.get('title', '').strip()
        existing_id = row.get('existing_FOODON_ID', '').strip()
        notes = row.get('notes', '').strip()

        label = f"row {n} (species={species_name!r}, menu={menu_name!r}, title={title!r})"

        if not species_name or not menu_name or not title or not existing_id:
            report.append(f"[ERROR] {label}: missing a required column (species/menu_name/title/existing_FOODON_ID)")
            continue

        s = species_by_name.get(species_name)
        if not s:
            report.append(f"[ERROR] {label}: species {species_name!r} not found in plants.tsv - check spelling")
            continue

        candidates = [d for d in menu_list if d.get('title', '') == title and d.get('name', '') == menu_name]
        if not candidates:
            report.append(f"[ERROR] {label}: no menus.tsv row with title={title!r} under menu={menu_name!r} - check exact spelling/spacing")
            continue

        template_id = candidates[0].get('meaning', '').strip()
        pp_lookup_id = template_id
        if not template_id:
            report.append(f"[ERROR] {label}: menus.tsv has a blank 'meaning' for this title - not ready to use yet")
            continue

        if pp_lookup_id not in template_by_id:
            report.append(f"[ERROR] {label}: ID {pp_lookup_id!r} not found in plant_parts.tsv")
            continue
        template_row = template_by_id[pp_lookup_id]

        organism_already_in_id = False
        if '{anatomical_parent}' in template_id:
            raw_ap = s.get('anatomical_parent', '').strip()
            if not raw_ap:
                report.append(f"[ERROR] {label}: species has no anatomical_parent set yet - can't resolve the key")
                continue
            if re.search(r'/|,| or ', raw_ap):
                report.append(f"[ERROR] {label}: anatomical_parent {raw_ap!r} has multiple values - needs a curator to split into one value first")
                continue
            template_id = template_id.replace('{anatomical_parent}', raw_ap)
            if '{organism}' in template_id:
                id_species_label = s.get('Alternative_name', '').strip() or species_name
                template_id = template_id.replace('{organism}', id_species_label)
                organism_already_in_id = True

        sheet_id = template_id if organism_already_in_id else f"{template_id}-{species_name}"

        if sheet_id in existing_by_sheet_id:
            current = existing_by_sheet_id[sheet_id]
            current_foodon_id = current.get('FOODON_ID', '').strip()
            if current_foodon_id == existing_id:
                report.append(f"[SKIP - already present] {label}: Sheet_ID {sheet_id!r} already maps to {existing_id!r}, nothing to do")
            else:
                report.append(f"[CONFLICT - needs a human decision] {label}: Sheet_ID {sheet_id!r} already maps to "
                               f"{current_foodon_id!r}, but this staging row asks for {existing_id!r} - NOT changed, "
                               f"pick one manually")
            continue

        sheet_label_template = template_row.get('label', '')
        try:
            sheet_label = sheet_label_template.format(organism=species_name, organism_base=s.get('organism_base', '').strip() or species_name)
        except (KeyError, IndexError):
            sheet_label = sheet_label_template

        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Mapping_Type ('Manual match (curator)') already says what this row is - keep
        # Curation Notes empty unless the curator explicitly passed a note to attach.
        curation_note = notes if notes else ''

        new_row = {col: '' for col in mapping_header}
        new_row['Sheet_ID'] = sheet_id
        new_row['Sheet_Label'] = sheet_label
        new_row['FOODON_ID'] = existing_id
        new_row['FOODON_Label'] = find_label_for_id(ontology_text, existing_id) if ontology_text else ''
        new_row['Mapping_Type'] = 'Manual match (curator)'
        new_row['Curation Notes'] = curation_note
        if 'created' in new_row:
            new_row['created'] = stamp
        if 'contributor' in new_row:
            new_row['contributor'] = args.contributor

        to_append.append(new_row)
        report.append(f"[ADD] {label}: Sheet_ID {sheet_id!r} -> {existing_id!r} (label: {sheet_label!r})")

    print(f"\nChecked {len(staging_rows)} staging row(s):\n")
    for line in report:
        print(" ", line)

    added = sum(1 for l in report if l.startswith('[ADD]'))
    skipped = sum(1 for l in report if l.startswith('[SKIP'))
    conflicts = sum(1 for l in report if l.startswith('[CONFLICT'))
    errors = sum(1 for l in report if l.startswith('[ERROR'))
    print(f"\nSummary: {added} to add, {skipped} already present, {conflicts} conflict(s) needing a decision, {errors} error(s).")

    if not to_append:
        print("\nNothing to append. id_mapping_table.tsv was not touched.")
        return

    if args.dry_run:
        print(f"\n--dry-run: would append {len(to_append)} row(s) to {args.mapping}. Nothing was written.")
        return

    with open(args.mapping, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=mapping_header, delimiter='\t')
        for row in to_append:
            w.writerow(row)

    print(f"\nAppended {len(to_append)} new row(s) to {args.mapping}. Existing rows were not touched.")


# --------------------------------------------------
def assign_ID(input_mapping_list, Sheet_ID,id_int):
    res = next((item for item in input_mapping_list if item["Sheet_ID"] == Sheet_ID), None)
    new_id = True
    if res:
        new_id = False
        FOODON_ID = res['FOODON_ID']
        created = res['created']
        contributor = res['contributor']
        see_also = res['see_also']
        exact_syn = res['exact_syn']
        broad_syn = res['broad_syn']
        narrow_syn = res['narrow_syn']
        database_cross_reference = res['database_cross_reference']
        comment = res['comment']
        editor_note = res['editor_note']
        imported_from = res['imported_from']
        definition_source = res['definition_source']
        image = res['image']
        image_rights_holder = res['image_rights_holder']
        image_license = res['image_license']

    else:
        # Newly minted IDs need the "FOODON:" prefix - without it they're
        # bare numbers, not a valid CURIE, and robot template can't resolve
        # them ("UNKNOWN ENTITY ERROR could not interpret '00000001'").
        # This matches every already-assigned ID already in id_mapping_table.tsv.
        FOODON_ID = f'FOODON:{id_int:08d}'

#        if id_int < 10:
#            FOODON_ID = 'FOODON:0000000{}'.format(id_int)
#        elif id_int < 100:
#            FOODON_ID = 'FOODON:000000{}'.format(id_int)
#        elif id_int < 1000:
#            FOODON_ID = 'FOODON:00000{}'.format(id_int)
#        elif id_int < 10000:
#            FOODON_ID = 'FOODON:0000{}'.format(id_int)
#        elif id_int < 100000:
#            FOODON_ID = 'FOODON:000{}'.format(id_int)
#        elif id_int < 1000000:
#            FOODON_ID = 'FOODON:00{}'.format(id_int)
#        elif id_int < 10000000:
#            FOODON_ID = 'FOODON:0{}'.format(id_int)
#        elif id_int < 100000000:
#            FOODON_ID = 'FOODON:{}'.format(id_int)
        id_int += 1
        created = ''
        contributor = ''
        see_also = ''
        exact_syn = ''
        broad_syn = ''
        narrow_syn = ''
        database_cross_reference = ''
        comment = ''
        editor_note = ''
        imported_from = ''
        definition_source = ''
        image = ''
        image_rights_holder = ''
        image_license = ''

    OutTup = namedtuple("OutTup", ["FOODON_ID", "new_id", "id_int", "created", "contributor", "see_also", "exact_syn", "broad_syn", "narrow_syn", "database_cross_reference", "comment", "editor_note", "imported_from", "definition_source", "image", "image_rights_holder", "image_license"])
    return OutTup(
        FOODON_ID, new_id, id_int, created, contributor, see_also, exact_syn, broad_syn, narrow_syn, database_cross_reference, comment, editor_note, imported_from, definition_source, image, image_rights_holder, image_license
    )


# --------------------------------------------------
def main():
    """Create robot template from list of input species and generic template"""
    args = get_args()

    # PLANT-ONLY, standalone mode: --merge-id-reuse does ONLY this and exits -
    # no compile happens, nothing else below is touched.
    if args.merge_id_reuse:
        run_merge_id_reuse(args)
        return

    is_plant = (args.organism == 'plant')  # PLANT-ONLY flag - everything new below checks this.

    template_arg = args.template
    template_list = []
    species_list = []
    input_mapping_list = []
    menu_mapping_list = []  # PLANT-ONLY, stays empty for every other organism.

    # open and save template file as list of OrderedDict
    with open(template_arg, mode='r', encoding='utf-8-sig') as csvfile:
        reader = csv.DictReader(csvfile, delimiter='\t')
        if is_plant:
            next(reader, None)  # PLANT-ONLY: skip plant_parts.tsv's ROBOT-instruction row.
        for row in reader:
            template_list.append(row)

    # open and save input species file as list of OrderedDict
    with open(args.input, mode='r', encoding='utf-8-sig') as csvfile:
        reader = csv.DictReader(csvfile, delimiter='\t')
        if is_plant:
            next(reader, None)  # PLANT-ONLY: skip plants.tsv's ROBOT-instruction row.
        for row in reader:
            species_list.append(row)

    # open and save ID mapping file as list of OrderedDict
    with open(args.mapping, mode='r', encoding='utf-8-sig') as csvfile:
        reader = csv.DictReader(csvfile, delimiter='\t')
        for row in reader:
            input_mapping_list.append(row)

    if is_plant:
        # PLANT-ONLY: menus.tsv - maps each checked picklist item to its
        # plant_parts.tsv row. animal/seafood/fungus don't load this at all.
        with open(args.menu, mode='r', encoding='utf-8-sig') as csvfile:
            reader = csv.DictReader(csvfile, delimiter='\t')
            for row in reader:
                menu_mapping_list.append(row)
        for d in menu_mapping_list:
            d.update(title=d['title'].replace(',', ''))

        # PLANT-ONLY: plants.tsv has no Alternative_name column (animal-only
        # in the original design) - backfill so the shared field-formatting
        # logic below can read s['Alternative_name'] without a KeyError.
        # (plants.tsv DOES already have 'organism_parent', which is what
        # this script itself expects, so nothing needed there.)
        for s in species_list:
            s.setdefault('Alternative_name', '')

        # PLANT-ONLY: drop DISABLED rows from plant_parts.tsv entirely so
        # they can never be matched/compiled. Keep the pre-filter list around
        # too (disabled_template_ids) purely so the skip log below can tell
        # a genuinely-missing plant_parts.tsv row apart from one that exists
        # but is deliberately DISABLED=TRUE - the two used to produce the
        # same generic [NO PLANT_PARTS ROW] message, which was confusing to
        # debug (see Notes.md item #48).
        disabled_template_ids = {row.get('ID', '').strip() for row in template_list if row.get('DISABLED', '').strip() == 'TRUE' and row.get('ID', '').strip() != ''}
        template_list = [row for row in template_list if row.get('DISABLED', '').strip() != 'TRUE']

        # PLANT-ONLY: load the species-added-date log, if one was given.
        # Local file only (see -sa above) - never overwritten by live-fetch,
        # never rewritten wholesale, only appended to for genuinely new species.
        species_added_dates = {}
        newly_added_species = []  # (species_name, timestamp, contributor) tuples to append at the very end
        if args.species_added_log:
            try:
                with open(args.species_added_log, newline='', encoding='utf-8') as f:
                    for row in csv.reader(f, delimiter='\t'):
                        if len(row) >= 2 and row[0] and row[0] != 'species':
                            species_added_dates[row[0]] = row[1]
            except FileNotFoundError:
                pass  # first time this log is used - nothing to load yet

    # Temp make list for dicts of Sheet_ID and Sheet_Label for output mapping file
    mappings_list = []
    skipped_log = []  # PLANT-ONLY: human-readable reasons for anything plant couldn't generate.
    plant_terms_written = 0  # PLANT-ONLY: one count per term actually written to the output template below.

    # write out robot template tsv file
    with open(args.output, 'w', newline='', encoding='utf-8') as csv_file:
        writer = csv.writer(csv_file, delimiter='\t')
        #write header rows
        header_1 = ['ontology ID', 'parent class', 'label', 'Equivalence axiom', 'Subclass axiom', 'definition', 'comment', 'Exact Synonym', 'Broad Synonym', 'created', 'contributor', 'see also', 'curation_status', 'Alternative Label', 'Legacy Exact Synonym', 'Legacy Broad Synonym', 'Legacy Narrow Synonym', 'Database Cross Reference', 'Legacy Comment', 'Editors Note', 'Imported From', 'Definition Source', 'image', 'Image rights Holder', 'Image License']
        header_2 = ['ID', 'SC % SPLIT=|', 'AL rdfs:label@en', 'EC %', 'SC % SPLIT=|', 'AL IAO:0000115@en', 'AL rdfs:comment@en', 'AL oboInOwl:hasExactSynonym@en SPLIT=|', 'AL oboInOwl:hasBroadSynonym@en', 'AT dc:date^^xsd:dateTime', 'A dc:contributor SPLIT=|', 'A rdfs:seeAlso SPLIT=|', 'AI IAO:0000114', 'AL oboInOwl:hasExactSynonym@en', 'AL oboInOwl:hasExactSynonym@en SPLIT=|', 'AL oboInOwl:hasBroadSynonym@en SPLIT=|', 'AL oboInOwl:hasNarrowSynonym@en SPLIT=|', 'AI oboInOwl:hasDbXref SPLIT=|', 'AL rdfs:comment@en', 'AL IAO:0000116@en', 'AI IAO:0000412', 'AI IAO:0000119 SPLIT=|', 'AI schema:image', '>A dc:license', '>A dc:rightsHolder']
        writer.writerow(header_1)
        writer.writerow(header_2)

        if is_plant:
            # ==========================================================
            # Plant template script starts here. Everything below, all the
            # way down to the "else" further on, only runs when compiling
            # the plant module (-org plant). Animal, seafood, and fungus
            # skip straight past this whole section and run their own
            # logic underneath instead - see that section for them.
            # ==========================================================
            for s in species_list:
                # PLANT-ONLY: stamp this species' first-ever-compiled date, once,
                # if it isn't already recorded. Never updates an existing entry -
                # a species that's already logged keeps its original date forever,
                # even if it gets recompiled in a later run.
                if args.species_added_log and s.get('Compiled', '').strip() == 'TRUE':
                    species_name_for_log = s.get('species', '').strip()
                    if species_name_for_log and species_name_for_log not in species_added_dates:
                        added_stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                        species_added_dates[species_name_for_log] = added_stamp
                        newly_added_species.append((species_name_for_log, added_stamp, args.contributor))

                for menu_name in ('PlantMaterialMenu', 'PlantFruitMenu', 'PlantSeedMenu',
                                   'PlantLeafMenu', 'ShootSystemMenu', 'RootSystemMenu'):
                    if s.get('Compiled', '').strip() != 'TRUE':
                        continue
                    val = s.get(menu_name, '').strip()
                    if val == '':
                        continue

                    species_name = s.get('species', '?')
                    p = re.compile(r"(\(\w*\s*\w*\b)(,)(\w*\s*\w*\b\))")
                    result = re.sub(p, r'\1\3', val)
                    menu_selections_list = result.split(", ")

                    for ms in menu_selections_list:
                        ms = ms.replace('"', '').strip()
                        if not ms:
                            continue

                        organism_already_in_id = False

                        # Scope the menu-title lookup to THIS menu name too - menus.tsv
                        # can reuse a title across menus (e.g. '{organism}' under both
                        # FungusMenu and PlantFruitMenu).
                        candidates = [d for d in menu_mapping_list if d['title'] == ms and d['name'] == menu_name]
                        if not candidates:
                            skipped_log.append(f"[NO MENU MATCH] species={species_name!r} menu={menu_name} title={ms!r} - not found in menus.tsv under this menu name")
                            continue
                        template_id = candidates[0]['meaning']
                        pp_lookup_id = template_id  # unresolved ID text - matches plant_parts.tsv's literal ID column.

                        if template_id == '' or template_id == 'New FoodOn ID':
                            skipped_log.append(f"[NOT READY - no real ID yet] species={species_name!r} menu={menu_name} title={ms!r} - menus.tsv meaning is still {template_id!r}")
                            continue

                        # anatomical_parent resolution - reads the real FoodOn ID directly
                        # off this species' own row (anatomical_parent_id column in
                        # plants.tsv), rather than a separate lookup file.
                        # Never guess: skip cleanly if it can't be resolved.
                        if '{anatomical_parent}' in template_id:
                            raw_ap = s.get('anatomical_parent', '').strip()
                            if not raw_ap:
                                skipped_log.append(f"[BLANK anatomical_parent] species={species_name!r} menu={menu_name} title={ms!r}")
                                continue
                            if re.search(r'/|,| or ', raw_ap):
                                skipped_log.append(f"[MULTI-VALUE anatomical_parent] species={species_name!r} menu={menu_name} title={ms!r} -> {raw_ap!r} - deferred, needs curator to split into one value")
                                continue
                            # Sheet_ID key is built from the plain anatomical_parent TEXT (e.g.
                            # "pome fruit"), not the resolved anatomical_parent_id. The axiom
                            # itself already only ever needed the text (anatomical_parent_fmt,
                            # below) - the ID was only ever used here, to build a unique key, and
                            # requiring it meant a term couldn't even be generated until someone
                            # curated a real ontology ID for that species first. Using the text
                            # instead removes that pre-curation requirement entirely: any new
                            # combination just gets a fresh ID minted and recorded in the mapping
                            # output sheet like any other new term.
                            template_id = template_id.replace('{anatomical_parent}', raw_ap)
                            if '{organism}' in template_id:
                                id_species_label = s.get('Alternative_name', '').strip() or species_name
                                template_id = template_id.replace('{organism}', id_species_label)
                                organism_already_in_id = True

                        pp_matches = [d for d in template_list if d.get('ID', '').strip() == pp_lookup_id]
                        if not pp_matches:
                            if pp_lookup_id in disabled_template_ids:
                                skipped_log.append(f"[PLANT_PARTS ROW DISABLED] species={species_name!r} menu={menu_name} title={ms!r} -> ID {pp_lookup_id!r} exists in plant_parts.tsv but is marked DISABLED=TRUE")
                            else:
                                skipped_log.append(f"[NO PLANT_PARTS ROW] species={species_name!r} menu={menu_name} title={ms!r} -> ID {pp_lookup_id!r} not found")
                            continue
                        if len(pp_matches) > 1:
                            skipped_log.append(f"[AMBIGUOUS - {len(pp_matches)} rows share ID {pp_lookup_id!r}] species={species_name!r} menu={menu_name} title={ms!r}")
                            continue
                        item = pp_matches[0]

                        # --- from here down mirrors the original script's field
                        # formatting logic (same variable names, same shape) so
                        # it's easy to compare side by side with the else: branch
                        # below - kept as a separate copy rather than a shared
                        # helper, so the original path is 100% guaranteed untouched.
                        species_label = s['species']
                        alt_label = ''
                        if s.get('Alternative_name', '').strip():
                            species_label = s['Alternative_name']
                            alt_label = item['label'].format(organism=s['species'], organism_base=s['species'])

                        if item['parent class'] is None:
                            parent_class = ''
                        elif s.get('organism_parent') and item['ID'] == 'FOODON:00003004':
                            parent_class = s['organism_parent']
                        else:
                            parent_class = item['parent class'].format(organism=species_label, organism_base=s.get('organism_base', '').strip() or s['species'])

                        label = item['label'].format(organism=species_label, organism_base=s.get('organism_base', '').strip() or s['species'])
                        if label == parent_class:
                            parent_class = ''

                        if organism_already_in_id:
                            Sheet_ID = template_id
                        else:
                            Sheet_ID = template_id + '-' + s['species']

                        with open(args.identifier) as file:
                            id_int = int(file.readline())
                        file.close()

                        ID_res = assign_ID(input_mapping_list=input_mapping_list, Sheet_ID=Sheet_ID, id_int=id_int)
                        ID = ID_res.FOODON_ID

                        taxon = s.get('taxon', s.get('ID', ''))
                        organism_base = s.get('organism_base', '').strip() or s['species']
                        # Raw anatomical_parent text (e.g. "pome fruit") for readable label/
                        # definition formatting - separate from the resolved real ID used for
                        # the Sheet_ID/ID key above. Templates that don't reference
                        # {anatomical_parent} simply ignore this kwarg.
                        anatomical_parent_fmt = s.get('anatomical_parent', '').strip()

                        # PLANT SEED FIX (2026-10-16): the "{organism} seed" template
                        # (FOODON:00005762) normally reuses this species' anatomical_parent
                        # directly in its Equivalence/Subclass axiom and definition - correct
                        # only when the species has no separate whole-fruit term (e.g. lentil,
                        # whose anatomical_parent IS "seed"; or a future grain, whose
                        # anatomical_parent would be "caryopsis fruit"). It's wrong whenever the
                        # species ALSO has PlantFruitMenu's bare "{organism}" field checked (e.g.
                        # grape, avocado, watermelon...) - there, anatomical_parent names the
                        # whole CONTAINER fruit (berry fruit, pepo fruit, aggregate fruit...),
                        # and reusing it for the seed too makes "X seed" and plain "X" the same
                        # OWL class, which is wrong (a seed is a part of the fruit, not the fruit
                        # itself). Detected here from data already in plants.tsv - no new column
                        # needed: if the whole-fruit field is also checked for this species, fall
                        # back to the same generic "derives from plant seed" pattern
                        # PlantLeafMenu's "{organism} leaf" template already uses successfully,
                        # instead of reusing anatomical_parent. item is copied (never mutated in
                        # place) since it's the same shared plant_parts.tsv row dict looked up
                        # again for every other species - mutating it directly would corrupt the
                        # template for everyone compiled after this one.
                        if item.get('ID', '').strip() == 'FOODON:00005762':
                            fruit_menu_entries = [e.strip() for e in s.get('PlantFruitMenu', '').split(',')]
                            if '{organism}' in fruit_menu_entries:
                                item = dict(item)
                                item['Equivalence axiom'] = "'piece of {organism_base}' and 'derives from' some 'plant seed'"
                                item['Subclass axiom'] = "'piece of {organism_base}' and 'derives from' some 'plant seed'"
                                item['definition'] = 'A whole seed that derives from a{n} {organism}.'

                        if s['species'] == args.organism:
                            if item['Equivalence Override'] == 'TRUE':
                                equivalence = ''
                            elif item['Equivalence Override'] != '':
                                equivalence = item['Equivalence Override'].format(organism=s['species'], taxon=taxon, anatomical_parent=anatomical_parent_fmt)
                            elif item['Equivalence axiom'] is None:
                                equivalence = ''
                            else:
                                equivalence = item['Equivalence axiom'].format(organism=s['species'], organism_base=organism_base, taxon=taxon, anatomical_parent=anatomical_parent_fmt)
                        else:
                            if item['Equivalence axiom'] is None:
                                equivalence = ''
                            elif s.get('organism_parent') and item['ID'] == 'FOODON:00003004':
                                equivalence = item['Equivalence axiom'].format(organism=s['species'], organism_base=organism_base, taxon=taxon, anatomical_parent=anatomical_parent_fmt)
                                equivalence = equivalence.replace("animal", s['organism_parent'])
                            else:
                                equivalence = item['Equivalence axiom'].format(organism=species_label, organism_base=organism_base, taxon=taxon, anatomical_parent=anatomical_parent_fmt)

                        if item['Subclass axiom'] is None:
                            subclass_axiom = ''
                        else:
                            subclass_axiom = item['Subclass axiom'].format(organism=species_label, organism_base=organism_base, taxon=taxon, anatomical_parent=anatomical_parent_fmt)

                        # --- plants.tsv 'logic' column override (subclass vs equivalence axiom) ---
                        # plant_parts.tsv deliberately mirrors the 'Subclass axiom' column with the
                        # exact same text as 'Equivalence axiom' on template rows like the whole-plant,
                        # material, substance, piece-of-plant, anatomical_parent-based {organism}, and
                        # seed rows - not so both get written, but so whichever one the compile picks
                        # below is already correct either way. Default (blank 'logic') and 'only
                        # equivalency' both mean: use ONLY the equivalence axiom. Setting 'logic' to
                        # 'only subclassOf' is the one case that should use the subclass axiom instead -
                        # e.g. a genus-level-only NCBITaxon match (blackberry/dewberry both only having
                        # 'Rubus', a genus, not a species-level ID), where an equivalence axiom ('in
                        # taxon' some 'Rubus') incorrectly claims the term IS that whole genus; a
                        # subclass axiom (IS A KIND OF that genus) is the scientifically correct
                        # relationship instead. A curator flags this per-species by setting plants.tsv's
                        # 'logic' column - no separate tool or script needed, it's read automatically on
                        # every real compile.
                        logic_val = s.get('logic', '').strip().lower()
                        if logic_val == 'only subclassof':
                            # Same mirrored-column design as above: for most rows subclass_axiom
                            # is already identical to equivalence, so there's nothing to combine -
                            # just keep it as-is. Only actually append when subclass_axiom holds
                            # something genuinely different (e.g. still blank, or distinct text),
                            # otherwise this would glue the same sentence onto itself ("X and X").
                            if equivalence and equivalence != subclass_axiom:
                                subclass_axiom = (subclass_axiom + ' and ' + equivalence) if subclass_axiom else equivalence
                            equivalence = ''
                        else:
                            # blank/default, 'only equivalency', or anything else (e.g. the instruction
                            # row's own text, filtered out before reaching here) -> equivalence only.
                            subclass_axiom = ''

                        if item['definition'] is None:
                            definition = ''
                        else:
                            article = 'n' if s['species'][:1].lower() in ['a', 'e', 'i', 'o', 'u'] else ''
                            definition = item['definition'].format(organism=species_label, organism_base=organism_base, n=article, anatomical_parent=anatomical_parent_fmt).capitalize()

                        if item['comment'] is None:
                            comment = ''
                        else:
                            comment = item['comment'].format(organism=species_label, organism_base=organism_base, n=article, anatomical_parent=anatomical_parent_fmt)

                        if item['Exact Synonym'] is None:
                            exact_synonym = ''
                        else:
                            exact_synonym = item['Exact Synonym'].format(organism=species_label, organism_base=organism_base, anatomical_parent=anatomical_parent_fmt)

                        if item['Broad Synonym'] is None:
                            broad_synonym = ''
                        else:
                            broad_synonym = item['Broad Synonym'].format(organism=species_label, organism_base=organism_base, anatomical_parent=anatomical_parent_fmt)

                        if ID_res.created != '':
                            created = ID_res.created
                        else:
                            created = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                        if ID_res.contributor != '':
                            contributor = ID_res.contributor
                        else:
                            contributor = args.contributor

                        see_also = ID_res.see_also if ID_res.see_also != '' else ''
                        curation_status = 'ready for release'
                        legacy_exact_syn = ID_res.exact_syn if ID_res.exact_syn != '' else ''
                        legacy_broad_syn = ID_res.broad_syn if ID_res.broad_syn != '' else ''
                        legacy_narrow_syn = ID_res.narrow_syn if ID_res.narrow_syn != '' else ''
                        database_cross_reference = ID_res.database_cross_reference if ID_res.database_cross_reference != '' else ''
                        legacy_comment = ID_res.comment if ID_res.comment != '' else ''
                        editor_note = ID_res.editor_note if ID_res.editor_note != '' else ''

                        # PLANT-ONLY, only when --tag_new_terms is passed: mark brand-new
                        # terms so they're easy to find and remove in Protege before the
                        # real final compile. Never touches already-existing terms.
                        if args.tag_new_terms and ID_res.new_id:
                            demo_tag = 'DEMO/TEST TERM - newly minted by a test compile. Review and remove (or confirm) before the final run.'
                            editor_note = f"{editor_note} {demo_tag}".strip() if editor_note else demo_tag

                        imported_from = ID_res.imported_from if ID_res.imported_from != '' else ''
                        definition_source = ID_res.definition_source if ID_res.definition_source != '' else ''
                        image = ID_res.image if ID_res.image != '' else ''
                        image_rights_holder = ID_res.image_rights_holder if ID_res.image_rights_holder != '' else ''
                        image_license = ID_res.image_license if ID_res.image_license != '' else ''

                        if ID_res.new_id is True:
                            mapping_dict = {'Sheet_ID': Sheet_ID,
                                            'Sheet_Label': label,
                                            'FOODON_ID': ID_res.FOODON_ID,
                                            'FOODON_Label': label,
                                            'Mapping_Type': 'Exact String Match',
                                            'Curation Notes': '',
                                            'created': created,
                                            'contributor': args.contributor,
                                            'see_also': see_also,
                                            'exact_syn': legacy_exact_syn,
                                            'broad_syn': legacy_broad_syn,
                                            'narrow_syn': legacy_narrow_syn,
                                            'database_cross_reference': database_cross_reference,
                                            'comment': legacy_comment,
                                            'editor_note': editor_note,
                                            'imported_from': imported_from,
                                            'definition_source': definition_source,
                                            'image': image,
                                            'image_rights_holder': image_rights_holder,
                                            'image_license': image_license
                                            }
                            mappings_list.append(mapping_dict)
                            with open(args.identifier, 'w') as file:
                                file.write(str(ID_res.id_int))
                            file.close()

                        row = [ID, parent_class, label, equivalence, subclass_axiom, definition, comment, exact_synonym, broad_synonym, created, contributor, see_also, curation_status, alt_label, legacy_exact_syn, legacy_broad_syn, legacy_narrow_syn, database_cross_reference, legacy_comment, editor_note, imported_from, definition_source, image, image_rights_holder, image_license]
                        writer.writerow(row)
                        plant_terms_written += 1

        else:
            # ==========================================================
            # Animal, seafood, and fungus template script. This is the
            # original logic, unchanged - plant has its own separate
            # section right above and never runs any of this.
            # ==========================================================
            for s in species_list:
                template = s['template']

                for index, item in enumerate(template_list):
                    keep = ''
                    if template == 'top':
                        keep = args.organism
                    else:
                        keep = item[str(template)]

                    if keep != '' and s['Compiled'] == 'TRUE':
                        filter_species = keep.split("|")
                        filter_species = [i for i in filter_species if i]
                        filter_species = [i.strip() for i in filter_species]
                        if s['species'] in filter_species or s['Alternative_name'] in filter_species or 'ALL' in filter_species:
                            if item['DISABLED'] != 'TRUE':

                                species_label = s['species']
                                alt_label = ''
                                if s['Alternative_name'] in filter_species:
                                    species_label = s['Alternative_name']
                                    alt_label = item['label'].format(organism=s['species'], organism_base=s['species'])

                                if item['parent class'] is None:
                                    parent_class = ''
                                # Special case for row with FOODON:00003004 set parent class to be 'organism_parent' from species list
                                elif s['organism_parent'] and item['ID'] == 'FOODON:00003004':
                                    parent_class = s['organism_parent']
                                else:
                                    parent_class = item['parent class'].format(organism=species_label, organism_base=s['species'])

                                label = item['label'].format(organism=species_label, organism_base=s['species'])

                                # Remove subclass axioms pointing to self
                                if label == parent_class:
                                    parent_class = ''

                                # Generate sheet ID for new mapping table rows
                                Sheet_ID = item['ID'] + '-' + s['species']

                                # Continue ID range count from previous run
                                with open(args.identifier) as file:
                                    id_int = int(file.readline())
                                file.close()

                                # Call helper function to pull data from input_mapping_list as well as assign new IDs
                                ID_res = assign_ID(input_mapping_list=input_mapping_list, Sheet_ID=Sheet_ID, id_int=id_int)
                                ID = ID_res.FOODON_ID

                                # Equivalence axiom
                                if s['species'] == args.organism:
                                    if item['Equivalence Override'] == 'TRUE':
                                        equivalence = ''
                                    elif item['Equivalence Override'] != '':
                                        equivalence = item['Equivalence Override'].format(organism=s['species'], taxon=s['ID'])
                                    elif item['Equivalence axiom'] is None:
                                        equivalence = ''
                                    else:
                                        equivalence = item['Equivalence axiom'].format(organism=s['species'], organism_base=s['species'], taxon=s['ID'])
                                else:
                                    if item['Equivalence axiom'] is None:
                                        equivalence = ''
                                    # Special case for row with FOODON:00003004 and row has an Equivalence axiom
                                    elif s['organism_parent'] and item['ID'] == 'FOODON:00003004':
                                        equivalence = item['Equivalence axiom'].format(organism=s['species'], organism_base=s['species'], taxon=s['ID'])
                                        equivalence = equivalence.replace("animal", s['organism_parent'])
                                    else:
                                        equivalence = item['Equivalence axiom'].format(organism=species_label, organism_base=s['species'], taxon=s['ID'])

                                if item['Subclass axiom'] is None:
                                    subclass_axiom = ''
                                else:
                                    subclass_axiom = item['Subclass axiom'].format(organism=species_label, organism_base=s['species'])

                                if item['definition'] is None:
                                    definition = ''
                                else:
                                    if s['species'][0].lower() in ['a', 'e', 'i', 'o', 'u']:
                                        article = 'n'
                                    else:
                                        article = ''
                                    definition = item['definition'].format(organism=species_label, organism_base=s['species'], n=article).capitalize()

                                if item['comment'] is None:
                                    comment = ''
                                else:
                                    comment = item['comment'].format(organism=species_label, organism_base=s['species'], n=article)


                                if item['Exact Synonym'] is None:
                                    exact_synonym = ''
                                else:
                                    exact_synonym = item['Exact Synonym'].format(organism=species_label, organism_base=s['species']);

                                if item['Broad Synonym'] is None:
                                    broad_synonym = ''
                                else:
                                    broad_synonym = item['Broad Synonym'].format(organism=species_label, organism_base=s['species'])

                                if ID_res.created != '':
                                    created = ID_res.created
                                else:
                                    created = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                                if ID_res.contributor != '':
                                    contributor = ID_res.contributor
                                else:
                                    contributor = args.contributor

                                if ID_res.see_also != '':
                                    see_also = ID_res.see_also
                                else:
                                    see_also = ''

                                curation_status = 'ready for release'

                                if ID_res.exact_syn != '':
                                    legacy_exact_syn = ID_res.exact_syn
                                else:
                                    legacy_exact_syn = ''

                                if ID_res.broad_syn != '':
                                    legacy_broad_syn = ID_res.broad_syn
                                else:
                                    legacy_broad_syn = ''

                                if ID_res.narrow_syn != '':
                                    legacy_narrow_syn = ID_res.narrow_syn
                                else:
                                    legacy_narrow_syn = ''

                                if ID_res.database_cross_reference != '':
                                    database_cross_reference = ID_res.database_cross_reference
                                else:
                                    database_cross_reference = ''

                                if ID_res.comment != '':
                                    legacy_comment = ID_res.comment
                                else:
                                    legacy_comment = ''

                                if ID_res.editor_note != '':
                                    editor_note = ID_res.editor_note
                                else:
                                    editor_note = ''

                                if ID_res.imported_from != '':
                                    imported_from = ID_res.imported_from
                                else:
                                    imported_from = ''

                                if ID_res.definition_source != '':
                                    definition_source = ID_res.definition_source
                                else:
                                    definition_source = ''

                                if ID_res.image != '':
                                    image = ID_res.image
                                else:
                                    image = ''

                                if ID_res.image_rights_holder != '':
                                    image_rights_holder = ID_res.image_rights_holder
                                else:
                                    image_rights_holder = ''

                                if ID_res.image_license != '':
                                    image_license = ID_res.image_license
                                else:
                                    image_license = ''

                                # If a new ID was generated add it to the output mapping dict list
                                if ID_res.new_id is True:
                                    mapping_dict = {'Sheet_ID': Sheet_ID,
                                                    'Sheet_Label': label,
                                                    'FOODON_ID': ID_res.FOODON_ID,
                                                    'FOODON_Label': label,
                                                    'Mapping_Type': 'Exact String Match',
                                                    'Curation Notes': '',
                                                    'created': created,
                                                    'contributor': args.contributor,
                                                    'see_also': see_also,
                                                    'exact_syn': legacy_exact_syn,
                                                    'broad_syn': legacy_broad_syn,
                                                    'narrow_syn': legacy_narrow_syn,
                                                    'database_cross_reference': database_cross_reference,
                                                    'comment': legacy_comment,
                                                    'editor_note': editor_note,
                                                    'imported_from': imported_from,
                                                    'definition_source': definition_source,
                                                    'image': image,
                                                    'image_rights_holder': image_rights_holder,
                                                    'image_license': image_license
                                                    }
                                    mappings_list.append(mapping_dict)
                                    # Write generated identifier to txt file
                                    with open(args.identifier, 'w') as file:
                                        file.write(str(ID_res.id_int))
                                    file.close()

                                row = [ID, parent_class, label, equivalence, subclass_axiom, definition, comment, exact_synonym, broad_synonym, created, contributor, see_also, curation_status, alt_label, legacy_exact_syn, legacy_broad_syn, legacy_narrow_syn, database_cross_reference, legacy_comment, editor_note, imported_from, definition_source, image, image_rights_holder, image_license]
                                writer.writerow(row)

    if not mappings_list:
        print('')
        print('No new terms to be added.')
        print('')
    else:
        # Write out Mapping list tsv file
        with open(args.new_terms, 'w', encoding='utf8', newline='') as output_file:
            fc = csv.DictWriter(output_file, fieldnames=mappings_list[0].keys(), delimiter='\t')
            fc.writeheader()
            fc.writerows(mappings_list)

    if is_plant:
        # PLANT-ONLY: report what couldn't be generated instead of leaving it silent.
        # Always (re)write this file, even when nothing was skipped - otherwise a
        # stale file from a previous run (listing terms that are now fixed) would
        # be left sitting there, making it look like the problem is still there.
        with open('../imports/plant/plant_compile_skip_log.txt', 'w', encoding='utf-8') as f:
            if skipped_log:
                f.write('\n'.join(skipped_log) + '\n')
            else:
                f.write("[plant] All terms generated successfully - nothing was skipped.\n")
        plant_species_compiled = len([s for s in species_list if s.get('Compiled', '').strip() == 'TRUE'])
        if skipped_log:
            print(f"\n[plant] {len(skipped_log)} plant term(s) could NOT be generated for your selected species.")
            print("[plant] See 'plant_compile_skip_log.txt' in imports/plant/ for which species/terms were skipped and why.")
        else:
            print("\n[plant] All terms generated successfully for your selected species - nothing was skipped.")
        plant_new_terms = len(mappings_list)
        plant_reused_terms = plant_terms_written - plant_new_terms
        print(f"[plant] {plant_species_compiled} species compiled, {plant_terms_written} term(s) generated in total "
              f"({plant_new_terms} new, {plant_reused_terms} reusing an existing ID).")

        # PLANT-ONLY: two automatic curation checks, always run - see
        # check_genus_level_taxa()/check_term_overlaps() above for what each
        # one catches. Read-only: this never changes plants.tsv, plant_parts.tsv,
        # or id_mapping_table.tsv - it only writes plant_curation_flags.txt.
        curation_flags = check_genus_level_taxa(species_list) + check_term_overlaps(species_list, template_list, menu_mapping_list, input_mapping_list)
        with open('../imports/plant/plant_curation_flags.txt', 'w', encoding='utf-8') as f:
            if not curation_flags:
                f.write("[plant] Nothing flagged for review.\n")
            else:
                f.write("# Not errors - everything above compiled successfully. These are curation/science\n")
                f.write("# judgment calls (genus-level taxa, possible duplicate parts) worth a second look\n")
                f.write("# when you have time - nothing here is blocking or broken.\n")
                for line in curation_flags:
                    f.write(line + "\n")
        if curation_flags:
            print(f"[plant] {len(curation_flags)} item(s) flagged for review (not errors - everything compiled fine) - see 'plant_curation_flags.txt' in imports/plant/.")
        else:
            print("[plant] Nothing flagged for review.")

        # PLANT-ONLY: append any newly-stamped species-added dates to the local
        # log. Append-only - existing rows already in the file are never rewritten.
        if args.species_added_log and newly_added_species:
            log_already_exists = False
            try:
                with open(args.species_added_log, 'r', encoding='utf-8'):
                    log_already_exists = True
            except FileNotFoundError:
                log_already_exists = False
            with open(args.species_added_log, 'a', newline='', encoding='utf-8') as f:
                w = csv.writer(f, delimiter='\t')
                if not log_already_exists:
                    w.writerow(['species', 'date_added', 'contributor'])
                for species_name_logged, stamp_logged, contributor_logged in newly_added_species:
                    w.writerow([species_name_logged, stamp_logged, contributor_logged])
            print(f"[plant] Logged first-added date for {len(newly_added_species)} new species to {args.species_added_log}.")

        # PLANT-ONLY, REAL RUN ONLY: append one row to the compile-history log -
        # date, species compiled, total terms generated, and how many of those
        # were brand-new IDs this run (the rest were existing IDs reused).
        # Unlike species_added_log/skip_log/curation_flags, this file is meant
        # to be committed - it's the running record of "how many terms has the
        # plant module produced over time," so every real run adds to it, never
        # just the ones with something new to report.
        if args.compile_history_log:
            history_already_exists = False
            try:
                with open(args.compile_history_log, 'r', encoding='utf-8'):
                    history_already_exists = True
            except FileNotFoundError:
                history_already_exists = False
            with open(args.compile_history_log, 'a', newline='', encoding='utf-8') as f:
                w = csv.writer(f, delimiter='\t')
                if not history_already_exists:
                    # Plain text line, not a tsv row - written once, only when the file
                    # is first created. Starts with '#' so a spreadsheet/pandas read with
                    # comment='#' skips it automatically; a plain tsv reader just sees one
                    # extra line above the real header.
                    f.write("# Real runs (MODE=run) only - never test runs. One row is added automatically each time 'run_organism_parts.sh MODE=run MODULE=plant' completes.\n")
                    w.writerow(['date', 'species_compiled', 'terms_generated', 'new_ids_minted', 'terms_reused', 'contributor'])
                run_stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                w.writerow([run_stamp, plant_species_compiled, plant_terms_written, plant_new_terms, plant_reused_terms, args.contributor])
            print(f"[plant] Logged this run to {args.compile_history_log} - {plant_species_compiled} species, {plant_terms_written} terms ({plant_new_terms} brand-new, {plant_reused_terms} reused).")

# --------------------------------------------------
if __name__ == '__main__':
    main()
