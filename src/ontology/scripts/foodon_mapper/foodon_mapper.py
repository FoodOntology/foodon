"""
foodon_mapper.py

Maps food ingredient strings (from a CSV/TSV file or URL) to FoodOn
ontology terms using a pipeline of specialist recognisers followed by a
Claude-AI-assisted OWL search.

Pipeline (applied in order, first match wins):
  1. modules.nutrient_recognizer  – vitamins, minerals, macronutrients,
                                   fatty acids, and bioactives
  2. modules.sugar_recognizer     – sugars, sweeteners, syrups
  3. modules.fruit_recognizer     – fresh, dried, and processed fruit forms
  3b.modules.dairy_recognizer    – milk, cream, butter, cheese, yogurt, and other dairy
                                   (with "whole" prefix detection →
                                    FOODON:03430150 naturally shaped whole form)
  4. modules.chemical_recognizer  – food chemicals: additives (FOODON:03412972),
                                   mixtures (CHEBI:60004), and other non-nutrient
                                   chemical entities
  (further modules: organism, anatomy, …)
  5. Claude AI parse → FoodOn OWL search  (general fallback, not yet implemented)

Output formats
──────────────
  Default (no --format):   tab-delimited columns to stdout (or -o file)
  --format markdown        Markdown report  → report.md   (or -o file)
  --format html            HTML report      → report.html (or -o file)

TSV columns:
  ingredient      – original input string
  match_status    – exact | parent | partial | no_match
  matched_id      – primary ontology ID matched
  matched_label   – label for the primary match
  matched_terms   – semicolon-separated  category:label [ID]
                    e.g. "nutrient:thiamine hydrochloride [CHEBI:49105]"
  unmatched_terms – semicolon-separated  kind:text fragments
                    e.g. "color:black; adjective:organic"
  source          – module that produced the match
  notes           – form hints (hint:label [ID]) and warnings

Status meanings:
  exact    – specific ontology term matched the full ingredient string
  parent   – broader group/parent term matched; specific form is likely
             a narrower child not yet named in the ontology
  partial  – one or more component terms matched, not the full ingredient
  no_match – nothing matched

Unmatched term prefixes:
  color, size, texture, flavor  – known characteristic types
  adjective   – modifier of unknown type (Characteristic class in FoodOn)
  unresolved  – noun-like fragment awaiting a future recogniser module

Usage:
    python3 foodon_mapper.py -i example.tsv
    python3 foodon_mapper.py -i example.tsv -o out.tsv
    python3 foodon_mapper.py -i example.tsv --format markdown
    python3 foodon_mapper.py -i example.tsv --format html
    python3 foodon_mapper.py -i "https://docs.google.com/…/export?format=csv" --format html

Author: Damion Dooley 2026
"""

import argparse
import csv
import html as html_mod
import io
import os
import re
import sys
from datetime import date
from typing import Optional

OBO = 'http://purl.obolibrary.org/obo/'

sys.path.insert(0, os.path.dirname(__file__))

from modules.nutrient_recognizer import recognize_nutrient, recognize_vitamin, NutrientMatch, VitaminMatch
from modules.sugar_recognizer import recognize_sugar, SugarMatch
from modules.fruit_recognizer import recognize_fruit, FruitMatch
from modules.chemical_recognizer import recognize_chemical, ChemicalMatch
recognize_additive = recognize_chemical  # backward-compat alias used in pipeline below
AdditiveMatch = ChemicalMatch
from modules.dairy_recognizer import recognize_dairy, DairyMatch
from modules.spice_recognizer import recognize_spice, SpiceMatch
from modules.herb_recognizer import recognize_herb, HerbMatch
from modules.seed_recognizer import recognize_seed, SeedMatch
from modules.grain_recognizer import recognize_grain, GrainMatch
from modules.characteristic_recognizer import recognize_characteristic, CharacteristicMatch
from modules.match_result import MatchResult, ComponentTerm, UnmatchedTerm, curie_to_iri


# ── Argument parser ─────────────────────────────────────────────────────────────

def init_parser() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Map food ingredient terms to FoodOn ontology terms.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('-i', '--input', required=True,
        help='Input file (CSV/TSV) or Google Sheets export URL')
    parser.add_argument('-o', '--output',
        help='Output file path. Default: stdout for TSV, report.md or report.html for --format')
    parser.add_argument('--format', dest='fmt', choices=['markdown', 'html'],
        help='Output format (default: tab-delimited TSV to stdout)')
    parser.add_argument('-c', '--column', default='ingredient',
        help='Ingredient column name (default: ingredient)')
    parser.add_argument('--top', type=int, default=1,
        help='Number of top candidate matches to return per ingredient (default: 1)')
    parser.add_argument('--owl',
        default=os.path.join(os.path.dirname(__file__), '..', 'cache-foodon-merged.owl'),
        help='Path to FoodOn merged OWL file')
    parser.add_argument('--api-key',
        help='Anthropic API key (default: $ANTHROPIC_API_KEY env var)')
    parser.add_argument('--no-cache', action='store_true',
        help='Disable Claude response caching')
    parser.add_argument('-v', '--verbose', action='store_true',
        help='Print per-ingredient processing details to stderr')
    parser.add_argument('--version', action='version', version='0.1.0')
    return parser.parse_args()


# ── Generic pipeline adapter ─────────────────────────────────────────────────────

def match_to_result(
    ingredient: str,
    m,
    *,
    category: str,
    source_module: str,
    match_status: str = 'exact',
) -> MatchResult:
    """
    Convert any recogniser match object into a unified MatchResult.

    All recogniser match dataclasses share the fields accessed here:
    matched_id, matched_label, residual_text, form_hints.
    Per-recogniser differences (category, source_module, match_status) are
    passed as keyword arguments by the pipeline.
    """
    component = ComponentTerm(
        category=category,
        label=m.matched_label,
        id=m.matched_id,
        iri=curie_to_iri(m.matched_id),
    )
    unmatched = [UnmatchedTerm('unresolved', m.residual_text)] if m.residual_text else []
    if unmatched and match_status not in ('no_match', 'partial'):
        match_status = 'partial'
    result = MatchResult(
        ingredient=ingredient,
        match_status=match_status,
        matched_id=m.matched_id,
        matched_label=m.matched_label,
        matched_iri=curie_to_iri(m.matched_id),
        component_terms=[component],
        unmatched_terms=unmatched,
        source_module=source_module,
    )
    result.form_hints = list(getattr(m, 'form_hints', []))
    return result


# Backward-compat aliases kept for any external callers
def nutrient_match_to_result(ingredient, vm):
    match_status = 'exact' if vm.match_type in ('chemical_form', 'annotated_form') else 'parent'
    return match_to_result(ingredient, vm, category='nutrient',
                           source_module='nutrient_recognizer', match_status=match_status)

vitamin_match_to_result = nutrient_match_to_result


# ── Plant / organism qualifier prefixes ────────────────────────────────────────
#
# "whole" and "wild" may appear as leading qualifiers on plant and organism
# ingredients.  Each is detected, stripped, and — if the remaining text
# matches a plant recogniser — injected as an additional ComponentTerm.
#
# FOODON:03430131 = "whole form (sensu food)" — parent class
# FOODON:03430150 = "naturally shaped whole form (sensu food)" — for countable
#   biological ingredients (whole fruit, whole chicken, etc.)
# FOODON:00005743 = "wild harvested organism material" — applies to any plant
#   or organism ingredient prefixed with "wild" (wild strawberry, wild garlic…)

_WHOLE_FORM_COMPONENT = ComponentTerm(
    category = 'characteristic',
    label    = 'naturally shaped whole form (sensu food)',
    id       = 'FOODON:03430150',
    iri      = curie_to_iri('FOODON:03430150'),
)

_WILD_HARVESTED_COMPONENT = ComponentTerm(
    category = 'characteristic',
    label    = 'wild harvested organism material',
    id       = 'FOODON:00005743',
    iri      = curie_to_iri('FOODON:00005743'),
)

_WHOLE_PREFIX_RE = re.compile(r'^whole\s+', re.IGNORECASE)
_WILD_PREFIX_RE  = re.compile(r'^wild\s+',  re.IGNORECASE)


def _strip_whole_prefix(text: str):
    """Return (True, remainder) if *text* starts with 'whole ', else (False, text)."""
    m = _WHOLE_PREFIX_RE.match(text)
    if m:
        return True, text[m.end():]
    return False, text


def _strip_plant_prefixes(text: str):
    """
    Strip 'wild' and/or 'whole' leading qualifiers from *text* in any order,
    returning (has_wild, has_whole, plant_text).

    Handles all orderings: "wild X", "whole X", "wild whole X", "whole wild X".
    Only plant-category recognisers (fruit, herb, spice) receive plant_text;
    nutrients, dairy, and additives always see the unstripped original.
    """
    has_wild  = False
    has_whole = False
    remaining = text
    changed   = True
    while changed:
        changed = False
        m = _WILD_PREFIX_RE.match(remaining)
        if m:
            has_wild  = True
            remaining = remaining[m.end():]
            changed   = True
        m = _WHOLE_PREFIX_RE.match(remaining)
        if m:
            has_whole = True
            remaining = remaining[m.end():]
            changed   = True
    return has_wild, has_whole, remaining


def _match_material_only(ingredient: str, options: argparse.Namespace) -> Optional[MatchResult]:
    """
    Run steps 1-8 (material recognisers only) for *ingredient*.

    Returns a MatchResult if any material recogniser fires, otherwise None.
    Called by match_ingredient for the primary pass and again by the
    characteristic-retry path (step 9) when a characteristic is found but no
    material has been identified yet.
    """
    has_wild, has_whole, plant_text = _strip_plant_prefixes(ingredient)

    # 1. Nutrient — full string only (nutrients are never prefixed with wild/whole)
    vm = recognize_nutrient(ingredient)
    if vm:
        match_status = 'exact' if vm.match_type in ('chemical_form', 'annotated_form') else 'parent'
        return match_to_result(ingredient, vm, category='nutrient',
                               source_module='nutrient_recognizer', match_status=match_status)

    sm = recognize_sugar(ingredient)
    if sm:
        return match_to_result(ingredient, sm, category='nutrient',
                               source_module='sugar_recognizer')

    # 2. Fruit — use plant_text (qualifiers stripped) when any were detected
    fm = recognize_fruit(plant_text if (has_wild or has_whole) else ingredient)
    if fm:
        result = match_to_result(ingredient, fm, category='food',
                                 source_module='fruit_recognizer')
        if has_whole:
            result.component_terms.insert(0, _WHOLE_FORM_COMPONENT)
        if has_wild:
            result.component_terms.insert(0, _WILD_HARVESTED_COMPONENT)
        return result

    # 3. Dairy — full string
    dm = recognize_dairy(ingredient)
    if dm:
        return match_to_result(ingredient, dm, category='dairy',
                               source_module='dairy_recognizer')

    # 4. Spice — use plant_text when wild/whole were detected
    spm = recognize_spice(plant_text if (has_wild or has_whole) else ingredient)
    if spm:
        result = match_to_result(ingredient, spm, category='spice',
                                 source_module='spice_recognizer')
        if has_wild:
            result.component_terms.insert(0, _WILD_HARVESTED_COMPONENT)
        return result

    # 5. Herb — use plant_text when wild/whole were detected
    hm = recognize_herb(plant_text if (has_wild or has_whole) else ingredient)
    if hm:
        result = match_to_result(ingredient, hm, category='food',
                                 source_module='herb_recognizer')
        if has_wild:
            result.component_terms.insert(0, _WILD_HARVESTED_COMPONENT)
        return result

    # 6. Seed — use plant_text when wild/whole were detected
    sem = recognize_seed(plant_text if (has_wild or has_whole) else ingredient)
    if sem:
        result = match_to_result(ingredient, sem, category='food',
                                 source_module='seed_recognizer')
        if has_wild:
            result.component_terms.insert(0, _WILD_HARVESTED_COMPONENT)
        return result

    # 7. Grain — cereal grains and pseudocereals (FOODON:00001093)
    gm = recognize_grain(ingredient)
    if gm:
        return match_to_result(ingredient, gm, category='food',
                               source_module='grain_recognizer')

    # 8. Additive — full string (additives are not countable biological entities)
    am = recognize_additive(ingredient)
    if am:
        return match_to_result(ingredient, am, category='additive',
                               source_module='chemical_recognizer')

    return None


def match_ingredient(ingredient: str, options: argparse.Namespace) -> MatchResult:
    """Run the recogniser pipeline for one ingredient string."""

    # Steps 1-8: material recognisers
    result = _match_material_only(ingredient, options)
    if result:
        return result

    # 9. Characteristic — fallback for quality/state adjectives (COB:0000502 subtree)
    #
    # Iteratively strip characteristic terms and retry material recognisers after
    # each removal.  Stopping conditions: material found, residual exhausted, or no
    # further characteristic can be stripped from the remaining text.
    #
    # Status rules for a material+characteristic result:
    #   composite – every token in the original string is accounted for
    #               (material found, no unmatched text)
    #   partial   – material found but some text still unmatched
    cm = recognize_characteristic(ingredient)
    if cm:
        collected_chars: list[ComponentTerm] = []
        current_cm = cm

        while True:
            collected_chars.append(ComponentTerm(
                category='characteristic',
                label=current_cm.matched_label,
                id=current_cm.matched_id,
                iri=curie_to_iri(current_cm.matched_id),
            ))
            residual = current_cm.residual_text

            if not residual:
                break  # all text consumed by characteristics; no material to find

            # Try material recognisers on what remains after stripping characteristics
            retry = _match_material_only(residual, options)
            if retry:
                retry.ingredient = ingredient
                for ct in reversed(collected_chars):
                    retry.component_terms.insert(0, ct)
                retry.match_status = 'composite' if not retry.unmatched_terms else 'partial'
                return retry

            # No material yet — try stripping another characteristic from the residual
            next_cm = recognize_characteristic(residual)
            if not next_cm or next_cm.residual_text == residual:
                break  # no progress; stop iterating
            current_cm = next_cm

        # Characteristic-only result (no material found after exhausting all stripping)
        return MatchResult(
            ingredient=ingredient,
            match_status='exact' if not current_cm.residual_text else 'partial',
            matched_id=collected_chars[0].id,
            matched_label=collected_chars[0].label,
            matched_iri=collected_chars[0].iri,
            component_terms=collected_chars,
            unmatched_terms=(
                [UnmatchedTerm('unresolved', current_cm.residual_text)]
                if current_cm.residual_text else []
            ),
            source_module='characteristic_recognizer',
        )

    # TODO: Claude AI general fallback — decompose with Claude, search FoodOn OWL

    return MatchResult(
        ingredient=ingredient,
        match_status='no_match',
        matched_id='',
        matched_label='',
        matched_iri='',
        component_terms=[],
        unmatched_terms=[UnmatchedTerm('unresolved', ingredient)],
        source_module='none',
    )


# ── TSV writer ──────────────────────────────────────────────────────────────────

_TSV_FIELDS = [
    'ingredient', 'match_status', 'matched_id', 'matched_label',
    'food_material', 'characteristics', 'unmatched_terms', 'source', 'notes',
]


def _split_components(component_terms: list) -> tuple[list, list]:
    """Split component_terms into (food_material_terms, characteristic_terms)."""
    food = [t for t in component_terms if t.category != 'characteristic']
    chars = [t for t in component_terms if t.category == 'characteristic']
    return food, chars


def write_report_tsv(results: list, options: argparse.Namespace, out_fh) -> None:
    writer = csv.DictWriter(out_fh, fieldnames=_TSV_FIELDS,
                            delimiter='\t', extrasaction='ignore')
    writer.writeheader()
    for r in results:
        hint_notes = '; '.join(f'hint:{h.label} [{h.id}]' for h in r.form_hints)
        extra_notes = '; '.join(r.notes)
        all_notes = '; '.join(filter(None, [hint_notes, extra_notes]))
        food_terms, char_terms = _split_components(r.component_terms)
        writer.writerow({
            'ingredient':      r.ingredient,
            'match_status':    r.match_status,
            'matched_id':      r.matched_id,
            'matched_label':   r.matched_label,
            'food_material':   '; '.join(str(t) for t in food_terms),
            'characteristics': '; '.join(str(t) for t in char_terms),
            'unmatched_terms': '; '.join(str(t) for t in r.unmatched_terms),
            'source':          r.source_module,
            'notes':           all_notes,
        })


# ── Markdown writer ─────────────────────────────────────────────────────────────

def _md_cell(text: str) -> str:
    return str(text).replace('|', '\\|').replace('\n', ' ')


def _md_terms(terms: list) -> str:
    return '; '.join(_md_cell(str(t)) for t in terms)


def _md_iri_link(id_str: str, iri: str) -> str:
    if iri:
        return f'[{_md_cell(id_str)}]({iri})'
    return _md_cell(id_str)


def write_report_md(results: list, options: argparse.Namespace, out_fh) -> None:
    counts: dict[str, int] = {'exact': 0, 'parent': 0, 'composite': 0, 'partial': 0, 'no_match': 0}
    for r in results:
        counts[r.match_status] = counts.get(r.match_status, 0) + 1
    total = len(results)
    today = date.today().isoformat()

    out_fh.write('# FoodOn Ingredient Mapping Report\n\n')
    out_fh.write(
        f'**Input:** `{options.input}` · '
        f'**Column:** `{options.column}` · '
        f'**Generated:** {today}\n\n'
    )
    out_fh.write('---\n\n')

    out_fh.write('## Results\n\n')
    out_fh.write('| Ingredient | Status | Food material | Characteristics | Unmatched Terms |\n')
    out_fh.write('|:-----------|:------:|:--------------|:----------------|:----------------|\n')

    _status_label = {
        'exact': 'exact', 'parent': 'parent',
        'composite': 'composite', 'partial': 'partial', 'no_match': 'no\\_match',
    }
    for r in results:
        food_terms, char_terms = _split_components(r.component_terms)
        out_fh.write(
            f'| {_md_cell(r.ingredient)} '
            f'| {_status_label.get(r.match_status, r.match_status)} '
            f'| {_md_terms(food_terms)} '
            f'| {_md_terms(char_terms)} '
            f'| {_md_terms(r.unmatched_terms)} |\n'
        )

    out_fh.write(
        f'\n**{total}** ingredient{"s" if total != 1 else ""} processed: '
        f'**{counts["exact"]} exact**, '
        f'**{counts["parent"]} parent**, '
        f'**{counts.get("composite", 0)} composite**, '
        f'**{counts.get("partial", 0)} partial**, '
        f'**{counts["no_match"]} no\\_match**\n'
    )

    hinted = [r for r in results if r.form_hints]
    if hinted:
        out_fh.write('\n---\n\n## Form Hints\n\n')
        out_fh.write(
            'Ingredients matched as a general group term. More specific chemical forms '
            'used in food supplements or fortified foods are suggested below.\n\n'
        )
        for r in hinted:
            out_fh.write(
                f'### {_md_cell(r.ingredient)} '
                f'\u2192 {_md_cell(r.matched_label)} '
                f'({_md_iri_link(r.matched_id, r.matched_iri)})\n\n'
            )
            out_fh.write('| Form | ID | Note | Reference |\n')
            out_fh.write('|:-----|:---|:-----|:----------|\n')
            for h in r.form_hints:
                ref = f'[NIH ODS]({h.see_also})' if getattr(h, 'see_also', '') else ''
                out_fh.write(
                    f'| {_md_cell(h.label)} '
                    f'| {_md_iri_link(h.id, getattr(h, "iri", ""))} '
                    f'| {_md_cell(h.note)} '
                    f'| {ref} |\n'
                )
            out_fh.write('\n')

    unmatched_results = [r for r in results if r.match_status == 'no_match']
    if unmatched_results:
        n = len(unmatched_results)
        out_fh.write(f'\n---\n\n## Unmatched Ingredients\n\n')
        out_fh.write(
            f'**{n}** ingredient{"s" if n != 1 else ""} could not be mapped '
            f'to any FoodOn term.\n\n'
        )
        for r in unmatched_results:
            out_fh.write(f'- {_md_cell(r.ingredient)}\n')
        out_fh.write('\n')


# ── HTML writer ─────────────────────────────────────────────────────────────────

_HTML_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  font-size: 14px; line-height: 1.5; color: #333;
  margin: 2rem auto; max-width: 1300px; padding: 0 1.5rem;
}
h1 { color: #2c5f2e; font-size: 1.6rem; margin-bottom: 0.25em; }
h2 {
  color: #2c5f2e; font-size: 1.15rem;
  border-bottom: 2px solid #c8e6c9;
  padding-bottom: 0.3em; margin: 2rem 0 0.8rem;
}
p.meta { color: #666; font-size: 0.88em; margin: 0.4rem 0 1.5rem; }
code { background: #f3f3f3; padding: 0.1em 0.35em; border-radius: 3px; font-size: 0.9em; }
.summary-bar {
  display: flex; flex-wrap: wrap; gap: 0.6rem; align-items: center;
  background: #f1f8e9; border: 1px solid #c8e6c9;
  border-radius: 6px; padding: 0.7rem 1.1rem; margin-bottom: 1.2rem;
}
.summary-bar .total { font-weight: 600; margin-right: 0.4rem; }
table { border-collapse: collapse; width: 100%; margin-bottom: 1rem; }
thead th {
  background: #2c5f2e; color: #fff;
  padding: 0.55em 0.9em; text-align: left; font-weight: 600; white-space: nowrap;
}
tbody td { padding: 0.4em 0.9em; border-bottom: 1px solid #e8e8e8; vertical-align: top; }
tbody tr:hover td { background: #f9fbe7; }
.badge {
  display: inline-block; padding: 0.15em 0.55em;
  border-radius: 3px; font-size: 0.76em; font-weight: 700;
  letter-spacing: 0.03em; white-space: nowrap;
}
.badge-exact      { background: #d4edda; color: #1a5c2a; }
.badge-parent     { background: #fff3cd; color: #7d5a00; }
.badge-composite  { background: #e8d5f5; color: #5a1a7d; }
.badge-partial    { background: #cce5ff; color: #004085; }
.badge-no-match   { background: #f8d7da; color: #721c24; }
.term { font-family: 'SFMono-Regular', Consolas, monospace; font-size: 0.82em; }
a { color: #1565c0; text-decoration: none; }
a:hover { text-decoration: underline; }
details {
  border: 1px solid #c8e6c9; border-radius: 6px;
  margin-bottom: 0.8rem; overflow: hidden;
}
summary {
  padding: 0.55rem 1rem; cursor: pointer;
  background: #f1f8e9; font-weight: 600; color: #2c5f2e;
  list-style: none; display: flex; align-items: center; gap: 0.5rem;
}
summary::before { content: '\u25b6'; font-size: 0.7em; transition: transform 0.15s; }
details[open] summary::before { transform: rotate(90deg); }
summary:hover { background: #e8f5e9; }
details[open] summary { border-bottom: 1px solid #c8e6c9; }
details > table { margin: 0; }
details > table thead th { background: #558b2f; }
ul.unmatched {
  columns: 3; column-gap: 2rem;
  list-style: disc; padding-left: 1.2rem;
  font-size: 0.9em;
}
ul.unmatched li { break-inside: avoid; padding: 0.1em 0; }
"""

_BADGE_CLASS = {
    'exact': 'exact', 'parent': 'parent',
    'composite': 'composite', 'partial': 'partial', 'no_match': 'no-match',
}


def _esc(text: str) -> str:
    return html_mod.escape(str(text))


def _badge(status: str) -> str:
    cls = _BADGE_CLASS.get(status, 'no-match')
    return f'<span class="badge badge-{cls}">{_esc(status)}</span>'


def _html_id_link(id_str: str, iri: str) -> str:
    if iri:
        return f'<a href="{_esc(iri)}" target="_blank">{_esc(id_str)}</a>'
    return _esc(id_str)


def _html_component(term: ComponentTerm) -> str:
    label_with_id = f'{_esc(term.label)} {_html_id_link(f"[{term.id}]", term.iri)}'
    return f'<span class="term">{_esc(term.category)}:{label_with_id}</span>'


def write_report_html(results: list, options: argparse.Namespace, out_fh) -> None:
    counts: dict[str, int] = {'exact': 0, 'parent': 0, 'composite': 0, 'partial': 0, 'no_match': 0}
    for r in results:
        counts[r.match_status] = counts.get(r.match_status, 0) + 1
    total = len(results)
    today = date.today().isoformat()

    p = []  # output parts

    p.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FoodOn Ingredient Mapping Report</title>
  <style>{_HTML_CSS}</style>
</head>
<body>
<h1>FoodOn Ingredient Mapping Report</h1>
<p class="meta">
  Input: <code>{_esc(options.input)}</code> &middot;
  Column: <code>{_esc(options.column)}</code> &middot;
  Generated: {today}
</p>
""")

    # Summary bar
    p.append('<div class="summary-bar">\n')
    p.append(f'  <span class="total">{total} ingredient{"s" if total != 1 else ""}</span>\n')
    for status in ('exact', 'parent', 'composite', 'partial', 'no_match'):
        n = counts.get(status, 0)
        if n:
            p.append(f'  {_badge(status)} &times; {n}\n')
    p.append('</div>\n')

    # Results table
    p.append('<h2>Results</h2>\n<table>\n')
    p.append('  <thead><tr>'
             '<th>Ingredient</th><th>Status</th>'
             '<th>Food material</th><th>Characteristics</th><th>Unmatched Terms</th>'
             '</tr></thead>\n  <tbody>\n')
    for r in results:
        food_terms, char_terms = _split_components(r.component_terms)
        food_html = '<br>'.join(_html_component(t) for t in food_terms)
        char_html = '<br>'.join(_html_component(t) for t in char_terms)
        unmatched_html = '<br>'.join(
            f'<span class="term">{_esc(str(t))}</span>' for t in r.unmatched_terms
        )
        p.append(
            f'    <tr>'
            f'<td>{_esc(r.ingredient)}</td>'
            f'<td>{_badge(r.match_status)}</td>'
            f'<td>{food_html}</td>'
            f'<td>{char_html}</td>'
            f'<td>{unmatched_html}</td>'
            f'</tr>\n'
        )
    p.append('  </tbody>\n</table>\n')

    # Form hints
    hinted = [r for r in results if r.form_hints]
    if hinted:
        p.append('<h2>Form Hints</h2>\n')
        p.append('<p>Ingredients matched as a general group term. '
                 'Suggested specific chemical forms for food supplement or fortification context:</p>\n')
        for r in hinted:
            summary_text = (
                f'{_esc(r.ingredient)} &rarr; '
                f'{_esc(r.matched_label)} '
                f'({_html_id_link(r.matched_id, r.matched_iri)})'
            )
            p.append(f'<details>\n  <summary>{summary_text}</summary>\n')
            p.append('  <table>\n    <thead><tr>'
                     '<th>Form</th><th>ID</th><th>Note</th><th>Reference</th>'
                     '</tr></thead>\n    <tbody>\n')
            for h in r.form_hints:
                see_also = getattr(h, 'see_also', '')
                ref_html = (
                    f'<a href="{_esc(see_also)}" target="_blank">NIH ODS</a>'
                    if see_also else ''
                )
                p.append(
                    f'      <tr>'
                    f'<td>{_esc(h.label)}</td>'
                    f'<td>{_html_id_link(h.id, getattr(h, "iri", ""))}</td>'
                    f'<td>{_esc(h.note)}</td>'
                    f'<td>{ref_html}</td>'
                    f'</tr>\n'
                )
            p.append('    </tbody>\n  </table>\n</details>\n')

    # Unmatched
    unmatched_results = [r for r in results if r.match_status == 'no_match']
    if unmatched_results:
        n = len(unmatched_results)
        p.append(f'<h2>Unmatched Ingredients</h2>\n')
        p.append(f'<p><strong>{n}</strong> ingredient{"s" if n != 1 else ""} '
                 f'could not be mapped to any FoodOn term.</p>\n')
        p.append('<ul class="unmatched">\n')
        for r in unmatched_results:
            p.append(f'  <li>{_esc(r.ingredient)}</li>\n')
        p.append('</ul>\n')

    p.append('</body>\n</html>\n')
    out_fh.write(''.join(p))


# ── Input loader ────────────────────────────────────────────────────────────────

def load_input(options: argparse.Namespace) -> list[dict]:
    """Load rows from a local CSV/TSV file or a URL. Returns list of row dicts."""
    src = options.input
    if src.startswith('http://') or src.startswith('https://'):
        try:
            import urllib.request
            with urllib.request.urlopen(src) as resp:
                content = resp.read().decode('utf-8')
            fh = io.StringIO(content)
            sep = ','
        except Exception as exc:
            print(f'ERROR: could not fetch URL: {exc}', file=sys.stderr)
            sys.exit(1)
    else:
        sep = ',' if src.endswith('.csv') else '\t'
        fh = open(src, newline='', encoding='utf-8')

    try:
        reader = csv.DictReader(fh, delimiter=sep)
        fieldnames = list(reader.fieldnames or [])
        if options.column not in fieldnames:
            print(
                f'ERROR: column "{options.column}" not found. '
                f'Available columns: {fieldnames}',
                file=sys.stderr,
            )
            sys.exit(1)
        return list(reader)
    finally:
        fh.close()


# ── Main pipeline ───────────────────────────────────────────────────────────────

def run_pipeline(options: argparse.Namespace) -> None:
    rows = load_input(options)

    results: list[MatchResult] = []
    for row in rows:
        ingredient = row[options.column].strip()
        if not ingredient:
            continue
        result = match_ingredient(ingredient, options)
        results.append(result)
        if options.verbose:
            tag = (f'{result.match_status} [{result.matched_id}]'
                   if result.matched_id else result.match_status)
            print(f'  {ingredient!r:50s} → {tag}', file=sys.stderr)

    # Determine output path and writer
    fmt = options.fmt  # None | 'markdown' | 'html'

    if fmt is None:
        # Default: TSV to stdout (or -o file)
        if options.output:
            out_fh = open(options.output, 'w', newline='', encoding='utf-8')
            write_report_tsv(results, options, out_fh)
            out_fh.close()
            print(f'Report written to {options.output}', file=sys.stderr)
        else:
            write_report_tsv(results, options, sys.stdout)

    elif fmt == 'markdown':
        out_path = options.output or 'report.md'
        with open(out_path, 'w', encoding='utf-8') as out_fh:
            write_report_md(results, options, out_fh)
        print(f'Report written to {out_path}', file=sys.stderr)

    elif fmt == 'html':
        out_path = options.output or 'report.html'
        with open(out_path, 'w', encoding='utf-8') as out_fh:
            write_report_html(results, options, out_fh)
        print(f'Report written to {out_path}', file=sys.stderr)


def main() -> None:
    options = init_parser()
    run_pipeline(options)


if __name__ == '__main__':
    main()
