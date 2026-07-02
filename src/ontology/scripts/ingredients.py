"""
ingredients.py

Maps food ingredient strings (from a CSV/TSV file or URL) to FoodOn
ontology terms using a pipeline of specialist recognisers followed by a
Claude-AI-assisted OWL search.

Pipeline (applied in order, first match wins):
  1. nutrient       – vitamins, minerals, macronutrients, fatty acids, bioactives
  2. sweetener      – sugars, sugar alcohols, syrups, natural sweeteners
  3. fruit          – fresh, dried, and processed fruit forms
  4. root_vegetable – taproots, bulbs, corms, rhizomes, tubers
  5. dairy          – milk, cream, butter, cheese, yogurt, and other dairy
  6. spice          – spice food products (FOODON:03303380)
  7. herb           – herb food products (FOODON:00003042)
  8. seed           – plant seed food products (FOODON:00001173)
  9. grain          – cereal grain food products (FOODON:00001093)
  10. lipid         – cooking oils and animal fats (FOODON:03420190)
  11. chemical      – food additives (FOODON:03412972) and mixtures (CHEBI:60004)
  12. fermentation  – fermented foods (FOODON:00001258) and bacteria (NCBITaxon:2)
  13. characteristic – quality/state terms (COB:0000502) with material retry
  14. Claude AI parse → FoodOn OWL search  (general fallback, not yet implemented)

Coverage for each type is defined in ingredients.yaml configuration.

Output formats
──────────────
  Default (no --format):   tab-delimited columns to stdout (or -o file)
  --format markdown        Markdown report  → report.md   (or -o file)
  --format html            HTML report      → report.html (or -o file)

TSV columns:
  ingredient      – original input string
  match_status    – exact | parent | partial | no_match
  food_id         – semicolon-separated ontology IDs for matched food material terms
  food_material   – semicolon-separated labels for matched food material terms
  taxonomy        – organism CURIE for the matched food term (e.g. NCBITaxon:3750)
  type            – ingredient category (nutrient, food, additive, …)
  subtype         – recogniser sub-category (vitamin, emulsifier, …)
  matched_id      – primary ontology ID matched
  matched_label   – label for the primary match
  characteristics – semicolon-separated characteristic terms
  unmatched_terms – semicolon-separated  kind:text fragments
  source          – recogniser that produced the match
  notes           – child-entry comments and other warnings

Status meanings:
  exact     – a specific ontology term matched the full ingredient string
  parent    – broader group/parent term matched; specific form is likely
              a narrower child not yet named in the ontology
  composite – full string accounted for by material + characteristic term(s)
  partial   – one or more component terms matched, not the full ingredient
  no_match  – nothing matched

Usage:
    python3 ingredients.py -i example.tsv
    python3 ingredients.py -i example.tsv -o out.tsv
    python3 ingredients.py -i example.tsv --format markdown
    python3 ingredients.py -i example.tsv --format html
    python3 ingredients.py --type fruit "dried cranberry"
    python3 ingredients.py --type nutrient "vitamin C"

Author: Damion Dooley 2026
"""

import argparse
import csv
import html as html_mod
import io
import itertools
import os
import re
import subprocess
import sys
import yaml
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

OBO   = 'http://purl.obolibrary.org/obo/'
_HERE = os.path.dirname(os.path.abspath(__file__))


# ══════════════════════════════════════════════════════════════════════════════
# § 1  Result data model
# ══════════════════════════════════════════════════════════════════════════════

def curie_to_iri(curie: str) -> str:
    """Expand a CURIE like 'FOODON:00001014' to a full OBO IRI."""
    return OBO + curie.replace(':', '_')


def _taxonomy_iri(curie: str) -> str:
    """Return a web URL for a taxonomy CURIE (NCBITaxon, Wikidata, or OBO)."""
    if not curie:
        return ''
    if curie.startswith('NCBITaxon:'):
        return ('https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi'
                f'?id={curie.split(":", 1)[1]}')
    if curie.startswith('wd:'):
        return f'https://www.wikidata.org/wiki/{curie.split(":", 1)[1]}'
    return curie_to_iri(curie)


def _anatomy_iri(curie: str) -> str:
    """Return the OBO Foundry IRI for an anatomy CURIE (UBERON, PO, FAO, FOODON)."""
    return curie_to_iri(curie) if curie else ''


def _curie_label(curie: str) -> str:
    """Return the human-readable label for a CURIE from the DB, falling back to the CURIE itself."""
    if not curie:
        return curie
    return get_db().get(curie, {}).get('label', '') or curie


_EXT_ANATOMY_PREFIXES = frozenset(['UBERON', 'PO', 'FAO'])


def _anatomy_ref_curie(anatomy_curie: str) -> str:
    """Return the UBERON/PO/FAO CURIE to display for an anatomy term.

    - If the anatomy CURIE itself is UBERON/PO/FAO, return it directly.
    - If the anatomy entry has a pre-computed 'anatomy_ref' field (written by
      build_anatomy phase 3), return that.
    - Otherwise fall back to the anatomy CURIE as-is (FOODON label will be shown).
    """
    if not anatomy_curie:
        return anatomy_curie
    prefix = anatomy_curie.split(':')[0] if ':' in anatomy_curie else ''
    if prefix in _EXT_ANATOMY_PREFIXES:
        return anatomy_curie
    ref = get_db().get(anatomy_curie, {}).get('anatomy_ref', '')
    return ref if ref else anatomy_curie


@dataclass
class ComponentTerm:
    """A single matched ontology term with its semantic category."""
    category: str  # food | taxonomy | anatomy | nutrient | characteristic | process | additive | unknown
    label: str
    id: str
    iri: str

    def __str__(self) -> str:
        return f'{self.category}:{self.label} [{self.id}]'


@dataclass
class UnmatchedTerm:
    """A text fragment that could not be mapped to any ontology term."""
    kind: str   # color | size | texture | flavor | adjective | unresolved
    text: str

    def __str__(self) -> str:
        return f'{self.kind}:{self.text}'


@dataclass
class MatchResult:
    """Unified result from any recogniser."""
    ingredient: str
    match_status: str     # 'exact' | 'parent' | 'composite' | 'partial' | 'no_match'
    matched_id: str
    matched_label: str
    matched_iri: str
    component_terms: list # list[ComponentTerm]
    unmatched_terms: list # list[UnmatchedTerm]
    source_module: str
    type: str    = ''     # primary ingredient type: nutrient | fruit | grain | dairy | …
    subtype: str = ''     # sub-classification: vitamin | mineral | berry | milk | …
    taxonomy: str = ''    # organism CURIE from 'in taxon' link (e.g. NCBITaxon:3750)
    anatomy: str  = ''    # anatomical structure CURIE (e.g. UBERON:0000912)
    form_hints: list = field(default_factory=list)
    notes: list = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# § 2  Ingredient term database  (ingredients.yaml)
# ══════════════════════════════════════════════════════════════════════════════

_YAML = os.path.join(_HERE, 'ingredients.yaml')


def _load_yaml() -> dict:
    with open(_YAML, encoding='utf-8') as fh:
        return yaml.safe_load(fh)


_RAW:    dict = _load_yaml()
_CONFIG: dict = _RAW.get('configuration', {})

# Matches FoodOn's "principal term (attr1[, attr2])" label pattern.
_PAREN_PAT = re.compile(r'^(.+?)\s*\(([^)]+)\)$')


def _load_db() -> dict:
    db: dict = {}
    for entry_key, entry in (_RAW.get('ingredient') or {}).items():
        if not isinstance(entry, dict):
            continue
        entry = dict(entry)
        entry['id'] = entry_key
        db[entry_key] = entry
    return db


_DB: dict = _load_db()


def get_db() -> dict:
    """Return the full term database (all types)."""
    return _DB


# ---------------------------------------------------------------------------
# Assumption rewriting  (populated after normalize() is defined below)
# ---------------------------------------------------------------------------

_ASSUMPTIONS: dict[str, str] = {}


def _primary_type(entry: dict) -> str:
    """Return the primary (first) type of an entry.

    An entry's 'type' field may be a plain string or a list where the first
    element is the primary pipeline type and any subsequent elements are
    secondary/informational types (e.g. ['fruit', 'anatomy']).
    """
    t = entry.get('type', '')
    return t[0] if isinstance(t, list) and t else (t or '')


def _has_type(entry: dict, type_name: str) -> bool:
    """Return True if *type_name* is any of the entry's types (primary or secondary)."""
    t = entry.get('type', '')
    if isinstance(t, list):
        return type_name in t
    return t == type_name


def get_entries_by_type(type_name: str) -> dict:
    """Return entries whose **primary** type equals *type_name*.

    Secondary types (e.g. 'anatomy' on a fruit entry) are intentionally
    excluded so pipeline recognisers only see entries they own.
    """
    return {k: v for k, v in _DB.items() if _primary_type(v) == type_name}


def get_configuration(type_name: str = None) -> dict:
    """Return the configuration dict for *type_name*, or the full configuration."""
    if type_name is not None:
        return _CONFIG.get(type_name, {})
    return _CONFIG


def normalize(text: str) -> str:
    """Lower-case, collapse whitespace, strip."""
    return re.sub(r'\s+', ' ', text.strip().lower())


def _load_assumptions() -> dict[str, str]:
    raw = _RAW.get('assumptions') or {}
    if not isinstance(raw, dict):
        return {}
    return {normalize(k): str(v) for k, v in raw.items() if k and v}


_ASSUMPTIONS.update(_load_assumptions())


def apply_assumptions(ingredient: str) -> str:
    """Return the assumption-rewritten form of *ingredient*, or *ingredient* unchanged.

    If the normalized ingredient text matches a key in the assumptions dictionary
    exactly, the configured replacement string is returned.  Otherwise the original
    string is returned unmodified.

    Example: "egg" → "chicken egg"
    """
    replacement = _ASSUMPTIONS.get(normalize(ingredient))
    return replacement if replacement is not None else ingredient


def word_in(key: str, text: str) -> bool:
    """True if *key* appears as a whole-word unit within *text*."""
    return (text == key
            or text.startswith(key + ' ')
            or text.endswith(' ' + key)
            or (' ' + key + ' ') in text)


def strip_match(text: str, span: tuple) -> str:
    """Remove matched *span* from *text* and clean up surrounding punctuation."""
    before = text[:span[0]].rstrip(' ,;(')
    after  = text[span[1]:].lstrip(' ,;)')
    return re.sub(r'\s+', ' ', (before + ' ' + after).strip())


def _add_paren_variants(lookup, fk, parent_curie, form_label, child_id) -> None:
    """Add natural-order variants for "principal (attr1[, attr2])" labels."""
    pm = _PAREN_PAT.match(fk)
    if not pm:
        return
    principal = pm.group(1).strip()
    attrs = [a.strip() for a in pm.group(2).split(',')]
    if len(attrs) == 1:
        variant = attrs[0] + ' ' + principal
        if variant not in lookup:
            lookup[variant] = (parent_curie, form_label, child_id)
    elif len(attrs) == 2:
        for perm in itertools.permutations(attrs):
            variant = perm[0] + ' ' + perm[1] + ' ' + principal
            if variant not in lookup:
                lookup[variant] = (parent_curie, form_label, child_id)


_AI_SYN_STATUSES: frozenset = frozenset({'ok'})


def build_lookups(db: dict) -> tuple[dict, dict, dict]:
    """
    Build (alias_lookup, food_form_lookup, title_lookup) from any recogniser DB dict.

    Entries WITHOUT 'parent' → alias_lookup (label, title, synonyms).
    Entries WITH 'parent'    → food_form_lookup (label, title, synonyms + paren variants).
    Legacy 'food_forms' dict entries → food_form_lookup.
    title_lookup maps the raw (case-preserved) 'title' and 'label' strings to their
    db_key; used by the vitamin letter-pattern resolver and similar title-based dispatch.
    """
    alias_lookup:     dict[str, str]   = {}
    food_form_lookup: dict[str, tuple] = {}
    title_lookup:     dict[str, str]   = {}

    # Pre-pass: anatomy-typed entries (e.g. type:[grain,anatomy]) win over
    # pure food-product entries for the same synonym key.  This lets
    # "buckwheat seed" (type=[grain,anatomy]) claim "buckwheat" before
    # "buckwheat food product" (type=grain) can claim it via its title field.
    for db_key, entry in db.items():
        if entry.get('parent'):
            continue
        et = entry.get('type', '')
        tl = et if isinstance(et, list) else [et]
        if 'anatomy' not in tl:
            continue
        alias_lookup[entry['label'].lower()] = db_key
        t = entry.get('title', '')
        if t:
            alias_lookup[t.lower()] = db_key
        for alias in (entry.get('synonyms') or []):
            alias_lookup[alias.lower()] = db_key

    for db_key, entry in db.items():
        parents = entry.get('parent') or []
        if isinstance(parents, str):
            parents = [parents]

        if parents:
            parent_curie = parents[0]
            form_label   = entry['label']
            candidates   = [form_label, entry.get('title', '')] + list(entry.get('synonyms') or [])
            for text in candidates:
                if not text:
                    continue
                fk = text.lower()
                if fk not in food_form_lookup:
                    food_form_lookup[fk] = (parent_curie, form_label, db_key)
                _add_paren_variants(food_form_lookup, fk, parent_curie, form_label, db_key)
            for syn, syn_info in (entry.get('ai_synonyms') or {}).items():
                if isinstance(syn_info, dict) and syn_info.get('status') in _AI_SYN_STATUSES:
                    fk = normalize(syn)
                    if fk not in food_form_lookup:
                        food_form_lookup[fk] = (parent_curie, form_label, db_key)
        else:
            alias_lookup[entry['label'].lower()] = db_key
            title = entry.get('title', '')
            if title:
                tk = title.lower()
                if tk not in alias_lookup:
                    alias_lookup[tk] = db_key
            for alias in (entry.get('synonyms') or []):
                k = alias.lower()
                if k not in alias_lookup:
                    alias_lookup[k] = db_key
            for syn, syn_info in (entry.get('ai_synonyms') or {}).items():
                if isinstance(syn_info, dict) and syn_info.get('status') in _AI_SYN_STATUSES:
                    fk = normalize(syn)
                    if fk not in alias_lookup and fk not in food_form_lookup:
                        alias_lookup[fk] = db_key
            # title_lookup: case-preserved title and label for pattern-based dispatch
            for t in dict.fromkeys(filter(None, [title, entry.get('label', '')])):
                if t not in title_lookup:
                    title_lookup[t] = db_key

        for form_id, form_labels in (entry.get('food_forms') or {}).items():
            for form_label in (form_labels or []):
                fk = form_label.lower()
                if fk not in food_form_lookup:
                    food_form_lookup[fk] = (db_key, form_label, form_id)
                _add_paren_variants(food_form_lookup, fk, db_key, form_label, form_id)

    return alias_lookup, food_form_lookup, title_lookup


def strip_adjectives(normed: str, adjectives: frozenset) -> tuple[str, str]:
    """
    Iteratively strip known adjectives from the front of *normed*.
    Returns (core, stripped) where stripped is space-joined removed adjectives.
    """
    adjs_sorted = sorted(adjectives, key=len, reverse=True)
    removed = []
    text = normed
    changed = True
    while changed:
        changed = False
        for adj in adjs_sorted:
            if text.startswith(adj + ' '):
                removed.append(adj)
                text = text[len(adj) + 1:]
                changed = True
                break
    return text, ' '.join(removed)


# ══════════════════════════════════════════════════════════════════════════════
# § 3  Generic ingredient recognizer
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class IngredientMatch:
    """Generic result returned by recognize() for any ingredient type."""
    type:          str
    subtype:       object
    label:         str
    id:            str
    matched_label: str
    matched_id:    str
    match_type:         str   # canonical_name | alias | food_form | adjective_stripped | <suffix>_fallback
    raw_matched:        str
    residual_text:      str
    trailing_form_curie: str = ''   # CURIE of recognised trailing form word (syrup, paste…)
    form_hints:         list = field(default_factory=list)


_LOOKUP_CACHE: dict[str, tuple] = {}
_DB_CACHE:     dict[str, dict]  = {}


def _get_lookups(type_name: str) -> tuple[dict, dict, dict]:
    """Return (db, alias_lookup, food_form_lookup) for *type_name*, cached."""
    if type_name not in _LOOKUP_CACHE:
        db = get_entries_by_type(type_name)
        _DB_CACHE[type_name] = db
        _LOOKUP_CACHE[type_name] = build_lookups(db)
    alias_lookup, food_form_lookup, _ = _LOOKUP_CACHE[type_name]
    return _DB_CACHE[type_name], alias_lookup, food_form_lookup


def _try_food_form(normed, original, db, food_form_lookup, type_name):
    """Tier 1a: longest whole-word food-form match."""
    normed_s = normed[:-1] if normed.endswith('s') else None
    best_len, best_key = 0, None
    for form_key in food_form_lookup:
        if word_in(form_key, normed) or (normed_s and word_in(form_key, normed_s)):
            if len(form_key) > best_len:
                best_len, best_key = len(form_key), form_key
    if not best_key:
        return None

    parent_curie, form_label, form_id = food_form_lookup[best_key]
    parent = db.get(parent_curie, {})
    idx = normed.find(best_key)
    if idx == -1 and normed_s:
        idx = normed_s.find(best_key)
    end = idx + len(best_key)
    if end < len(normed) and normed[end] == 's' and end == len(normed) - 1:
        end += 1
    span = (idx, end)
    return IngredientMatch(
        type          = type_name,
        subtype       = parent.get('subtype', ''),
        label         = parent.get('label', form_label),
        id            = parent_curie,
        matched_label = form_label,
        matched_id    = form_id,
        match_type    = 'food_form',
        raw_matched   = original[span[0]:span[1]],
        residual_text = strip_match(original, span),
    )


def _try_alias(normed, original, db, alias_lookup, type_name):
    """Tier 1b: exact alias / canonical-name match (with simple -s deplural)."""
    normed_s = normed[:-1] if normed.endswith('s') else None
    akey = (normed   if normed   in alias_lookup else
            normed_s if normed_s in alias_lookup else None)
    if akey is None:
        return None

    curie = alias_lookup[akey]
    entry = db[curie]
    is_canonical = (akey == entry['label'].lower() or akey == curie.lower())
    return IngredientMatch(
        type          = type_name,
        subtype       = entry.get('subtype', ''),
        label         = entry['label'],
        id            = curie,
        matched_label = entry['label'],
        matched_id    = curie,
        match_type    = 'canonical_name' if is_canonical else 'alias',
        raw_matched   = original,
        residual_text = '',
    )


def _match_tiers(normed, original, db, alias_lookup, food_form_lookup, type_name, tier_order):
    """Try both tiers in the configured order."""
    if tier_order == 'food_form_first':
        return (
            _try_food_form(normed, original, db, food_form_lookup, type_name) or
            _try_alias(normed, original, db, alias_lookup, type_name)
        )
    return (
        _try_alias(normed, original, db, alias_lookup, type_name) or
        _try_food_form(normed, original, db, food_form_lookup, type_name)
    )


def recognize(type_name: str, text: str) -> Optional[IngredientMatch]:
    """
    Attempt to recognise an ingredient of *type_name* within *text*.

    Tiers:
      1/2  Direct food-form and alias matching in the configured order.
      3    Adjective-stripping + parenthetical reconstruction.
      4    Suffix fallback: append each configured fallback word and retry.
      5    Trailing-annotation strip: retry after removing a trailing (…) clause;
           the stripped content becomes residual_text, yielding a partial match.
    """
    normed = normalize(text)
    if not normed:
        return None

    db, alias_lookup, food_form_lookup = _get_lookups(type_name)
    cfg        = get_configuration(type_name)
    tier_order = cfg.get('tier_order', 'food_form_first')
    adjectives = frozenset(cfg.get('adjectives') or [])
    fallbacks  = list(cfg.get('fallbacks') or [])

    m = _match_tiers(normed, text, db, alias_lookup, food_form_lookup, type_name, tier_order)
    if m:
        return m

    remaining, stripped = strip_adjectives(normed, adjectives)
    if stripped:
        paren = remaining + ' (' + stripped.replace(' ', ', ') + ')'
        m = _match_tiers(paren, text, db, alias_lookup, food_form_lookup, type_name, tier_order)
        if m:
            m.match_type    = 'adjective_stripped'
            m.raw_matched   = text
            m.residual_text = ''
            return m

        m = _match_tiers(remaining, text, db, alias_lookup, food_form_lookup, type_name, tier_order)
        if m:
            m.match_type    = 'adjective_stripped'
            m.residual_text = stripped + (' ' + m.residual_text if m.residual_text else '')
            return m

    base = remaining if stripped else normed
    for suffix in fallbacks:
        m = _match_tiers(
            base + ' ' + suffix, text,
            db, alias_lookup, food_form_lookup, type_name, tier_order,
        )
        if m:
            m.match_type    = suffix + '_fallback'
            m.residual_text = stripped if stripped else ''
            m.raw_matched   = text
            return m

    # Tier 5: strip trailing preparation-form words (syrup, paste, powder…) and retry.
    # trailing_forms config may be a list (no CURIE) or a dict {word: curie}.
    # When a CURIE is provided the form word is itself a recognised food material
    # (composite); without a CURIE it becomes an unmatched residual (partial).
    trailing_forms_raw = cfg.get('trailing_forms') or []
    if isinstance(trailing_forms_raw, dict):
        trailing_forms_map = trailing_forms_raw        # {word: curie_or_empty}
    else:
        trailing_forms_map = {w: '' for w in trailing_forms_raw}

    for form_word in sorted(trailing_forms_map, key=len, reverse=True):
        if base.endswith(' ' + form_word):
            core_without_form = base[: -len(' ' + form_word)]
            if core_without_form:
                m = _match_tiers(
                    core_without_form, text,
                    db, alias_lookup, food_form_lookup, type_name, tier_order,
                )
                if m:
                    prior = (stripped + ' ' if stripped else '')
                    m.match_type         = 'trailing_form_stripped'
                    m.trailing_form_curie = trailing_forms_map[form_word] or ''
                    # If the form has a recognised CURIE it is a component, not residual.
                    if m.trailing_form_curie:
                        m.residual_text = prior.rstrip() + (' ' + m.residual_text if m.residual_text else '')
                    else:
                        m.residual_text = (
                            prior + form_word
                            + (' ' + m.residual_text if m.residual_text else '')
                        )
                    m.raw_matched = text
                    return m

    # Tier 6: strip trailing parenthetical annotation and retry
    pm = _PAREN_PAT.match(base)
    if pm:
        core        = pm.group(1).strip()
        annotation  = pm.group(2).strip()
        m = _match_tiers(core, text, db, alias_lookup, food_form_lookup, type_name, tier_order)
        if m:
            prior = (stripped + ' ' if stripped else '')
            m.residual_text = prior + annotation + (' ' + m.residual_text if m.residual_text else '')
            m.raw_matched   = text
            return m

    return None


# ══════════════════════════════════════════════════════════════════════════════
# § 4  Nutrient recognizer
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class FormHint:
    """Backward-compat stub — no longer populated; kept so existing callers don't break."""
    label: str
    id: str
    iri: str
    note: str
    see_also: str = ''


@dataclass
class NutrientMatch:
    """Result of recognising any nutrient term in an ingredient string."""
    type: str
    subtype: str       # 'vitamin'|'mineral'|'macronutrient'|'fatty_acid'|'bioactive'
    nutrient_key: str
    label: str
    id: str
    matched_label: str
    matched_id: str
    match_type: str    # 'canonical_name'|'abbreviation'|'chemical_form'|'alias'|'annotated_form'
    raw_matched: str
    residual_text: str
    form_hints: list = field(default_factory=list)


VitaminMatch = NutrientMatch


# ── Nutrient sub-databases ─────────────────────────────────────────────────────

_ALL_NUTRIENTS   = get_entries_by_type('nutrient')
VITAMIN_DB       = {k: v for k, v in _ALL_NUTRIENTS.items() if v.get('subtype') == 'vitamin'}
MINERAL_DB       = {k: v for k, v in _ALL_NUTRIENTS.items() if v.get('subtype') == 'mineral'}
MACRONUTRIENT_DB = {k: v for k, v in _ALL_NUTRIENTS.items() if v.get('subtype') == 'macronutrient'}
FATTY_ACID_DB    = {k: v for k, v in _ALL_NUTRIENTS.items() if v.get('subtype') == 'fatty_acid'}
BIOACTIVE_DB     = {k: v for k, v in _ALL_NUTRIENTS.items() if v.get('subtype') == 'bioactive'}


# ── Vitamin lookup tables ──────────────────────────────────────────────────────
#
# CDNO class hierarchy for minerals:
#   macro elements (CDNO:0000011): Ca, Cl, Mg, P, K, Na, S
#   trace elements (CDNO:0000012): Cr, Cu, F, I, Fe, Mn, Mo, Se, Zn
#
# Fatty acid CHEBI IRIs verified in OWL:
#   CHEBI:25681 = omega-3 fatty acid         CHEBI:36009 = omega-6 fatty acid
#   CHEBI:27432 = alpha-linolenic acid (ALA) CHEBI:17351 = linoleic acid (LA)
#   CHEBI:36005 = docosahexaenoic acid (DHA) CHEBI:28661 = gamma-linolenic acid (GLA)
#   CHEBI:36006 = icosapentaenoic acid (EPA) CHEBI:25413 = monounsaturated fatty acid
#   CHEBI:26208 = polyunsaturated fatty acid CHEBI:166968= trans-fatty acid

_VIT_ALIAS_LOOKUP, _VIT_FORM_LOOKUP, _VIT_TITLE_TO_CURIE = build_lookups(VITAMIN_DB)
_MIN_ALIAS, _MIN_FORM, _ = build_lookups(MINERAL_DB)
_MAC_ALIAS, _MAC_FORM, _ = build_lookups(MACRONUTRIENT_DB)
_FA_ALIAS,  _FA_FORM,  _ = build_lookups(FATTY_ACID_DB)
_BIO_ALIAS, _BIO_FORM, _ = build_lookups(BIOACTIVE_DB)


def _apply_synonyms_mode(mode: str) -> None:
    """Update ai_synonym inclusion mode and rebuild all lookup caches.

    mode='ok'       — only ai_synonyms with status='ok' are indexed (default)
    mode='proposed' — ai_synonyms with status='ok' or status='proposed' are indexed
    """
    global _AI_SYN_STATUSES
    global _VIT_ALIAS_LOOKUP, _VIT_FORM_LOOKUP, _VIT_TITLE_TO_CURIE
    global _MIN_ALIAS, _MIN_FORM, _MAC_ALIAS, _MAC_FORM
    global _FA_ALIAS, _FA_FORM, _BIO_ALIAS, _BIO_FORM
    statuses: set[str] = {'ok'}
    if mode == 'proposed':
        statuses.add('proposed')
    _AI_SYN_STATUSES = frozenset(statuses)
    _LOOKUP_CACHE.clear()
    _DB_CACHE.clear()
    _VIT_ALIAS_LOOKUP, _VIT_FORM_LOOKUP, _VIT_TITLE_TO_CURIE = build_lookups(VITAMIN_DB)
    _MIN_ALIAS, _MIN_FORM, _ = build_lookups(MINERAL_DB)
    _MAC_ALIAS, _MAC_FORM, _ = build_lookups(MACRONUTRIENT_DB)
    _FA_ALIAS,  _FA_FORM,  _ = build_lookups(FATTY_ACID_DB)
    _BIO_ALIAS, _BIO_FORM, _ = build_lookups(BIOACTIVE_DB)

# ── Vitamin patterns ───────────────────────────────────────────────────────────

_VIT_LETTER_PAT = re.compile(
    r'\b(vitamins?|vits?\.?)\s*'
    r'(b\s*(?:1[0-9]|[1-9])|[adeck](?:\d)?)',
    re.IGNORECASE
)
_AS_PAREN_PAT = re.compile(r'\(\s*as\s+([^)]+)\)', re.IGNORECASE)


def _resolve_letter(raw: str) -> Optional[str]:
    """Map uppercased vitamin letter string (spaces stripped) to a VITAMIN_DB key."""
    candidate = 'vitamin ' + raw
    curie = _VIT_TITLE_TO_CURIE.get(candidate)
    if curie:
        return curie
    if re.fullmatch(r'B\d+', raw):
        return None
    if re.fullmatch(r'K\d', raw):
        return _VIT_TITLE_TO_CURIE.get('vitamin K')
    if re.fullmatch(r'D\d', raw):
        return _VIT_TITLE_TO_CURIE.get('vitamin D')
    return None


def recognize_vitamin(text: str) -> Optional[NutrientMatch]:
    """Recognise a vitamin in *text*. Priority: chemical form > alias > letter pattern."""
    normed = normalize(text)

    normed_s = normed[:-1] if normed.endswith('s') else None
    best_len, best_key = 0, None
    for form_key in _VIT_FORM_LOOKUP:
        if word_in(form_key, normed) or (normed_s is not None and word_in(form_key, normed_s)):
            if len(form_key) > best_len:
                best_len, best_key = len(form_key), form_key

    if best_key:
        letter, form_label, form_id = _VIT_FORM_LOOKUP[best_key]
        vit  = VITAMIN_DB[letter]
        idx  = normed.find(best_key)
        end  = idx + len(best_key)
        if end < len(normed) and normed[end] == 's' and end == len(normed) - 1:
            end += 1
        span = (idx, end)
        return NutrientMatch(
            type          = 'nutrient',
            subtype       = 'vitamin',
            nutrient_key  = letter,
            label         = vit['label'],
            id            = vit['id'],
            matched_label = form_label,
            matched_id    = form_id,
            match_type    = 'chemical_form',
            raw_matched   = text[span[0]:span[1]],
            residual_text = strip_match(text, span),
        )

    _akey = normed if normed in _VIT_ALIAS_LOOKUP else (
        normed[:-1] if normed.endswith('s') and normed[:-1] in _VIT_ALIAS_LOOKUP else None)
    if _akey is not None:
        letter = _VIT_ALIAS_LOOKUP[_akey]
        vit    = VITAMIN_DB[letter]
        return NutrientMatch(
            type          = 'nutrient',
            subtype       = 'vitamin',
            nutrient_key  = letter,
            label         = vit['label'],
            id            = vit['id'],
            matched_label = vit['label'],
            matched_id    = vit['id'],
            match_type    = 'abbreviation',
            raw_matched   = text,
            residual_text = '',
        )

    pat_m = _VIT_LETTER_PAT.search(normed)
    if pat_m:
        raw_letter  = re.sub(r'\s+', '', pat_m.group(2)).upper()
        vitamin_key = _resolve_letter(raw_letter)
        if vitamin_key and vitamin_key in VITAMIN_DB:
            vit      = VITAMIN_DB[vitamin_key]
            span     = pat_m.span()
            residual = strip_match(normed, span)

            form_match = None
            as_m = _AS_PAREN_PAT.search(normed[span[1]:])
            if as_m:
                form_text = normalize(as_m.group(1))
                if form_text in _VIT_FORM_LOOKUP and _VIT_FORM_LOOKUP[form_text][0] == vitamin_key:
                    form_match = _VIT_FORM_LOOKUP[form_text]
                    paren_end  = span[1] + as_m.end()
                    residual   = strip_match(normed, (span[0], paren_end))

            if form_match:
                _, form_label, form_id = form_match
                return NutrientMatch(
                    type          = 'nutrient',
                    subtype       = 'vitamin',
                    nutrient_key  = vitamin_key,
                    label         = vit['label'],
                    id            = vit['id'],
                    matched_label = form_label,
                    matched_id    = form_id,
                    match_type    = 'annotated_form',
                    raw_matched   = text[span[0]:span[1] + (as_m.end() if as_m else 0)],
                    residual_text = residual,
                )
            return NutrientMatch(
                type          = 'nutrient',
                subtype       = 'vitamin',
                nutrient_key  = vitamin_key,
                label         = vit['label'],
                id            = vit['id'],
                matched_label = vit['label'],
                matched_id    = vit['id'],
                match_type    = 'canonical_name',
                raw_matched   = text[pat_m.start():pat_m.end()],
                residual_text = residual,
            )

    return None


def _recognize_from_db(text, db, alias_lookup, form_lookup, category):
    """Generic alias + chemical-form recognizer for mineral/macro/FA/bioactive DBs."""
    normed = normalize(text)

    _akey = normed if normed in alias_lookup else (
        normed[:-1] if normed.endswith('s') and normed[:-1] in alias_lookup else None)
    if _akey is not None:
        key   = alias_lookup[_akey]
        entry = db[key]
        is_canonical = (_akey == entry['label'].lower() or _akey == key.lower())
        return NutrientMatch(
            type          = 'nutrient',
            subtype       = category,
            nutrient_key  = key,
            label         = entry['label'],
            id            = entry['id'],
            matched_label = entry['label'],
            matched_id    = entry['id'],
            match_type    = 'canonical_name' if is_canonical else 'alias',
            raw_matched   = text,
            residual_text = '',
        )

    normed_s = normed[:-1] if normed.endswith('s') else None
    best_len, best_key = 0, None
    for form_key in form_lookup:
        if word_in(form_key, normed) or (normed_s is not None and word_in(form_key, normed_s)):
            if len(form_key) > best_len:
                best_len, best_key = len(form_key), form_key

    if best_key:
        db_key, form_label, form_id = form_lookup[best_key]
        entry = db[db_key]
        idx   = normed.find(best_key)
        end   = idx + len(best_key)
        if end < len(normed) and normed[end] == 's' and end == len(normed) - 1:
            end += 1
        span  = (idx, end)
        return NutrientMatch(
            type          = 'nutrient',
            subtype       = category,
            nutrient_key  = db_key,
            label         = entry['label'],
            id            = entry['id'],
            matched_label = form_label,
            matched_id    = form_id,
            match_type    = 'chemical_form',
            raw_matched   = text[span[0]:span[1]],
            residual_text = strip_match(text, span),
        )

    return None


def recognize_mineral(text):
    return _recognize_from_db(text, MINERAL_DB, _MIN_ALIAS, _MIN_FORM, 'mineral')

def recognize_macronutrient(text):
    return _recognize_from_db(text, MACRONUTRIENT_DB, _MAC_ALIAS, _MAC_FORM, 'macronutrient')

def recognize_fatty_acid(text):
    return _recognize_from_db(text, FATTY_ACID_DB, _FA_ALIAS, _FA_FORM, 'fatty_acid')

def recognize_bioactive(text):
    return _recognize_from_db(text, BIOACTIVE_DB, _BIO_ALIAS, _BIO_FORM, 'bioactive')


def recognize_nutrient(text: str) -> Optional[NutrientMatch]:
    """Unified nutrient recognizer — try all categories in priority order."""
    return (
        recognize_vitamin(text)
        or recognize_mineral(text)
        or recognize_macronutrient(text)
        or recognize_fatty_acid(text)
        or recognize_bioactive(text)
    )


def describe_match(m: NutrientMatch) -> str:
    """Return a human-readable description of a NutrientMatch."""
    cat_tag = f'[{m.subtype}]'
    if m.matched_id != m.id:
        line = (
            f'{cat_tag} {m.label} [{m.id}]'
            f' → specific form: {m.matched_label} [{m.matched_id}]'
            f'  (via {m.match_type})'
        )
    else:
        line = (
            f'{cat_tag} {m.matched_label} [{m.matched_id}]'
            f'  (via {m.match_type})'
        )
    if m.residual_text:
        line += f'\n  residual: "{m.residual_text}"'
    return line


# ══════════════════════════════════════════════════════════════════════════════
# § 5  Characteristic recognizer
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CharacteristicMatch:
    """Result of recognising a quality / characteristic term."""
    type: str
    subtype: str
    label: str
    id: str
    matched_label: str
    matched_id: str
    match_type: str   # 'canonical_name'|'alias'|'supplemental_alias'
    raw_matched: str
    residual_text: str
    form_hints: list = field(default_factory=list)


_SUPPLEMENTAL: dict[str, str] = {
    # viability
    'live':                     'PATO:0001421',
    'living':                   'PATO:0001421',
    'deceased':                 'PATO:0001422',
    'non-viable':               'PATO:0001422',
    'killed':                   'PATO:0001422',
    # thermal
    'refrigerated':              'FOODON:00004725',
    'cold':                      'FOODON:00004725',
    'deep frozen':               'PATO:0001985',
    'deep-frozen':               'PATO:0001985',
    'individually quick frozen': 'PATO:0001985',
    'iqf':                       'PATO:0001985',
    'quick frozen':              'PATO:0001985',
    'quick-frozen':              'PATO:0001985',
    'blast frozen':              'FOODON:00004728',
    'blast-frozen':              'FOODON:00004728',
    'flash-frozen':              'FOODON:00004728',
    'defrosted':                 'FOODON:00004727',
    'thawed from frozen':        'FOODON:00004727',
    # processing
    'uncooked':          'FOODON:00004348',
    'unprocessed':       'FOODON:00004348',
    'unprepared':        'FOODON:00004348',
    'dry':               'FOODON:00004723',
    'dehydrated':        'FOODON:00004723',
    'desiccated':        'FOODON:00004723',
    'sun-dried':         'FOODON:00004723',
    'sun dried':         'FOODON:00004723',
    'air-dried':         'FOODON:00004723',
    'air dried':         'FOODON:00004723',
    'oven-dried':        'FOODON:00004723',
    'oven dried':        'FOODON:00004723',
    'cooked':            'FOODON:03440022',
    'heated':            'FOODON:03440022',
    'thermally treated': 'FOODON:03440022',
    'pasteurized':       'FOODON:03440022',
    'pasteurised':       'FOODON:03440022',
    'sterilized':        'FOODON:03440022',
    'sterilised':        'FOODON:03440022',
    'uht treated':       'FOODON:03440022',
    'uht':               'FOODON:03440022',
    'fully cooked':      'FOODON:03440014',
    'unpasteurized':     'FOODON:03440003',
    'unpasteurised':     'FOODON:03440003',
    'untreated':         'FOODON:03440003',
    'raw milk':          'FOODON:03440003',
    # physical form
    'chopped':   'FOODON:03430115',
    'diced':     'FOODON:03430115',
    'sliced':    'FOODON:03430137',
    'ground':    'FOODON:03430136',
    'minced':    'FOODON:03430136',
    'powdered':  'FOODON:03430136',
    'crushed':   'FOODON:03430136',
    'halved':    'FOODON:03430116',
    'quartered': 'FOODON:03430148',
    'wedged':    'FOODON:03430133',
    # ripeness
    'immature (fruit)': 'FOODON:03530051',
    'green (unripe)':   'FOODON:03530051',
    'ripened':          'FOODON:03530052',
    'mature (fruit)':   'FOODON:03530052',
    'over-ripe':        'FOODON:00003346',
    'over ripe':        'FOODON:00003346',
    'too ripe':         'FOODON:00003346',
    'partially ripe':   'FOODON:00003347',
    'semi-ripe':        'FOODON:00003347',
    # doneness
    'rare':              'FOODON:00005621',
    'rare cooked':       'FOODON:00005621',
    'extra rare':        'FOODON:00005672',
    'blue rare':         'FOODON:00005672',
    'very rare':         'FOODON:00005672',
    'medium-rare':       'FOODON:00005669',
    'medium rare':       'FOODON:00005669',
    'medium done':       'FOODON:00005671',
    'medium cooked':     'FOODON:00005671',
    'medium-well':       'FOODON:00005673',
    'medium well':       'FOODON:00005673',
    'well done':         'FOODON:00005615',
    'well-done':         'FOODON:00005615',
    'fully cooked meat': 'FOODON:00005615',
}

_CHAR_LOOKUP:    dict[str, tuple] = {}
_CHAR_LABEL_MAP: dict[str, str]   = {}


def _char_short(iri: str) -> str:
    return iri.replace(OBO, '').replace('_', ':')


def _iri_to_curie(iri: str) -> str:
    """Convert a full OBO IRI to a CURIE (e.g. 'http://.../obo/CHEBI_12345' → 'CHEBI:12345')."""
    return _char_short(iri)


def _build_char_lookup(owl_path: str) -> None:
    """Load COB:0000502 descendants from *owl_path* and populate _CHAR_LOOKUP."""
    try:
        from rdflib import Graph, URIRef, RDFS
        from collections import deque
    except ImportError:
        return

    ROOT       = 'http://purl.obolibrary.org/obo/COB_0000502'
    DEPRECATED = URIRef('http://www.w3.org/2002/07/owl#deprecated')
    EXACT_SYN  = URIRef('http://www.geneontology.org/formats/oboInOwl#hasExactSynonym')
    EXCLUDED   = {
        'http://purl.obolibrary.org/obo/BFO_0000017',   # realizable entity
        'http://purl.obolibrary.org/obo/PATO_0103000',  # quantitative
    }

    import logging
    _rdflib_log = logging.getLogger('rdflib.term')
    _prev_level = _rdflib_log.level
    _rdflib_log.setLevel(logging.ERROR)
    g = Graph()
    try:
        g.parse(owl_path)
    finally:
        _rdflib_log.setLevel(_prev_level)

    def lbl(iri):  return str(next(g.objects(URIRef(iri), RDFS.label), ''))
    def syns(iri): return [str(s) for s in g.objects(URIRef(iri), EXACT_SYN)]
    def dep(iri):  return (URIRef(iri), DEPRECATED, None) in g

    children: dict[str, list] = {}
    for s, _, o in g.triples((None, RDFS.subClassOf, None)):
        children.setdefault(str(o), []).append(str(s))

    queue   = deque([ROOT])
    visited = {ROOT} | EXCLUDED
    while queue:
        node = queue.popleft()
        for child in children.get(node, []):
            if child in visited:
                continue
            visited.add(child)
            if dep(child):
                continue
            queue.append(child)
            label = lbl(child)
            if not label:
                continue
            sid   = _char_short(child)
            entry = (sid, label)
            _CHAR_LABEL_MAP[sid] = label
            key = label.lower()
            if key not in _CHAR_LOOKUP:
                _CHAR_LOOKUP[key] = entry
            for syn in syns(child):
                skey = syn.lower()
                if skey not in _CHAR_LOOKUP:
                    _CHAR_LOOKUP[skey] = entry


def _build_char_supplemental() -> None:
    """Overlay _SUPPLEMENTAL aliases onto _CHAR_LOOKUP."""
    for alias, sid in _SUPPLEMENTAL.items():
        key = alias.lower()
        if key in _CHAR_LOOKUP:
            continue
        label = _CHAR_LABEL_MAP.get(sid, sid)
        _CHAR_LOOKUP[key] = (sid, label)


_DEFAULT_OWL = os.path.join(_HERE, 'cache-foodon-merged.owl')

if os.path.exists(_DEFAULT_OWL):
    _build_char_lookup(_DEFAULT_OWL)

_build_char_supplemental()


def recognize_characteristic(
    ingredient: str,
    owl_path: Optional[str] = None,
) -> Optional[CharacteristicMatch]:
    """
    Return a CharacteristicMatch if *ingredient* matches any term under
    COB:0000502 (characteristic), otherwise None.  Longest-substring match wins.
    """
    global _CHAR_LOOKUP
    if not _CHAR_LOOKUP and owl_path and os.path.exists(owl_path):
        _build_char_lookup(owl_path)
        _build_char_supplemental()

    text = re.sub(r'\s+', ' ', ingredient.strip().lower())
    if not text:
        return None

    best_key: Optional[str] = None
    for key in _CHAR_LOOKUP:
        if re.search(r'\b' + re.escape(key) + r'\b', text):
            if best_key is None or len(key) > len(best_key):
                best_key = key

    if best_key is None:
        return None

    sid, label = _CHAR_LOOKUP[best_key]
    residual = text.replace(best_key, '').strip(' ,;-')
    is_supplemental = best_key in _SUPPLEMENTAL
    is_canonical    = (best_key == label.lower() and not is_supplemental)

    return CharacteristicMatch(
        type          = 'characteristic',
        subtype       = '',
        label         = label,
        id            = sid,
        matched_label = label,
        matched_id    = sid,
        match_type    = (
            'supplemental_alias' if is_supplemental else
            'canonical_name'     if is_canonical     else
            'alias'
        ),
        raw_matched   = best_key,
        residual_text = residual,
    )


# ══════════════════════════════════════════════════════════════════════════════
# § 6  Children-by-parent notes index
# ══════════════════════════════════════════════════════════════════════════════
#
# Maps each parent CURIE to child entries that carry a 'comment' field.
# Used by report writers to populate the Notes column.

_CHILDREN_BY_PARENT: dict[str, list] = {}
for _c_curie, _c_entry in get_db().items():
    _c_comment = _c_entry.get('comment', '')
    if not _c_comment:
        continue
    _c_parents = _c_entry.get('parent') or []
    if isinstance(_c_parents, str):
        _c_parents = [_c_parents]
    for _c_parent in _c_parents:
        _CHILDREN_BY_PARENT.setdefault(_c_parent, []).append({
            'label':   _c_entry.get('label', _c_curie),
            'curie':   _c_curie,
            'iri':     curie_to_iri(_c_curie),
            'comment': _c_comment,
        })


# ── Taxonomy lookup (for residual resolution) ─────────────────────────────────
#
# Maps normalized organism name → CURIE for all type:taxonomy entries.
# Built once at module init; used by _resolve_residuals() for O(1) lookup
# without going through the full recognize() machinery.

def _build_taxonomy_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for curie, entry in _DB.items():
        if not _has_type(entry, 'taxonomy'):
            continue
        candidates = ([entry.get('label', ''), entry.get('title', '')]
                      + list(entry.get('synonyms') or [])
                      + list(entry.get('common_names') or []))
        for name, info in (entry.get('ai_common_names') or {}).items():
            if isinstance(info, dict) and info.get('status') == 'ok':
                candidates.append(name)
        for text in filter(None, candidates):
            k = normalize(text)
            if k not in lookup:
                lookup[k] = curie
    return lookup


_TAXONOMY_LOOKUP: dict[str, str] = _build_taxonomy_lookup()


def _build_anatomy_lookup() -> dict[str, str]:
    # Only include entries whose PRIMARY type is anatomy.
    # Food materials that have anatomy as a secondary type (e.g. type: [fruit, anatomy])
    # are food ingredients first and must not pollute the anatomy residual lookup.
    lookup: dict[str, str] = {}
    for curie, entry in _DB.items():
        if _primary_type(entry) != 'anatomy':
            continue
        candidates = ([entry.get('label', ''), entry.get('title', '')]
                      + list(entry.get('synonyms') or []))
        for text in filter(None, candidates):
            k = normalize(text)
            if k not in lookup:
                lookup[k] = curie
    return lookup


_ANATOMY_LOOKUP: dict[str, str] = _build_anatomy_lookup()


# ══════════════════════════════════════════════════════════════════════════════
# § 7  Pipeline
# ══════════════════════════════════════════════════════════════════════════════

# ── Argument parser ────────────────────────────────────────────────────────────

def init_parser() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Map food ingredient terms to FoodOn ontology terms.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # Pipeline mode
    parser.add_argument('-i', '--input',
        help='Input file (CSV/TSV) or Google Sheets export URL (pipeline mode)')
    parser.add_argument('-o', '--output',
        help='Output file path. Default: stdout for TSV, report.md or report.html for --format')
    parser.add_argument('--format', dest='fmt', metavar='FMT',
        help='Output format(s): tsv, markdown, html (comma-separated for multiple; '
             'default: tsv to stdout)')
    parser.add_argument('-c', '--column', default='ingredient',
        help='Ingredient column name (default: ingredient)')
    # Type-test mode
    parser.add_argument('--type', metavar='TYPE',
        help='Test a single ingredient type (e.g. fruit, grain, dairy). '
             'Runs in type-test mode instead of full pipeline.')
    parser.add_argument('ingredient', nargs='?',
        help='Single ingredient string to test (use with --type)')
    parser.add_argument('--tsv', metavar='FILE',
        help='TSV/CSV file to scan (use with --type)')
    parser.add_argument('--sep', default='\t',
        help='Field separator for --tsv (default: tab)')
    parser.add_argument('--top', type=int, default=1,
        help='Number of top candidate matches to return per ingredient (default: 1)')
    parser.add_argument('-f', '--fresh', action='store_true',
        help='Regenerate cache-foodon-merged.owl from foodon-edit.ofn via ROBOT '
             '(merge → reason ELK → relax --include-subclass-of) before running')
    parser.add_argument('--owl',
        default=_DEFAULT_OWL,
        help='Path to FoodOn merged OWL file')
    parser.add_argument('--api-key',
        help='Anthropic API key (default: $ANTHROPIC_API_KEY env var)')
    parser.add_argument('--no-cache', action='store_true',
        help='Disable Claude response caching')
    parser.add_argument('-v', '--verbose', action='store_true',
        help='Print per-ingredient processing details to stderr')
    parser.add_argument('--version', action='version', version='0.1.0')
    parser.add_argument('--rebuild', metavar='TYPE|all',
        help='Rebuild/refresh ingredients.yaml entries for a configuration type '
             '(e.g. characteristic, nutrient, chemical) or "all" for every type. '
             'Types with no OWL roots are reported and skipped.')
    parser.add_argument('--dry-run', action='store_true',
        help='With --rebuild: show what would change without writing to disk')
    parser.add_argument('--synonyms', choices=['ok', 'proposed'], default='ok',
        help='ai_synonyms statuses to include when building lookup indices: '
             '"ok" (default) includes only reviewed synonyms; '
             '"proposed" also includes proposed synonyms pending review.')
    return parser.parse_args()


# ── Generic pipeline adapter ───────────────────────────────────────────────────

def match_to_result(
    ingredient: str,
    m,
    *,
    category: str,
    source_module: str,
    match_status: str = 'exact',
) -> MatchResult:
    """Convert any recogniser match object into a unified MatchResult."""
    component = ComponentTerm(
        category=category,
        label=m.matched_label,
        id=m.matched_id,
        iri=curie_to_iri(m.matched_id),
    )
    unmatched = [UnmatchedTerm('unresolved', m.residual_text)] if m.residual_text else []
    if unmatched and match_status not in ('no_match', 'partial'):
        match_status = 'partial'
    # A fallback-suffix match means the completing word wasn't in the original string;
    # downgrade to 'parent' so callers know the match was inferred.
    if match_status == 'exact' and getattr(m, 'match_type', '').endswith('_fallback'):
        match_status = 'parent'

    # If a trailing form word has a recognised CURIE, add it as a component term.
    form_curie = getattr(m, 'trailing_form_curie', '') or ''
    extra_components: list[ComponentTerm] = []
    if form_curie:
        _fdb = get_db()
        form_entry = _fdb.get(form_curie, {})
        extra_components.append(ComponentTerm(
            category='food',
            label=form_entry.get('label', form_curie),
            id=form_curie,
            iri=curie_to_iri(form_curie),
        ))
        match_status = 'composite'

    result = MatchResult(
        ingredient=ingredient,
        match_status=match_status,
        matched_id=m.matched_id,
        matched_label=m.matched_label,
        matched_iri=curie_to_iri(m.matched_id),
        component_terms=[component] + extra_components,
        unmatched_terms=unmatched,
        source_module=source_module,
        type=getattr(m, 'type', category) or category,
        subtype=getattr(m, 'subtype', '') or '',
    )
    result.form_hints = list(getattr(m, 'form_hints', []))
    _mdb = get_db()
    result.taxonomy = (
        _mdb.get(getattr(m, 'id', ''), {}).get('taxonomy', '')
        or _mdb.get(m.matched_id, {}).get('taxonomy', '')
    )
    result.anatomy = (
        _mdb.get(getattr(m, 'id', ''), {}).get('anatomy', '')
        or _mdb.get(m.matched_id, {}).get('anatomy', '')
    )
    return result


# Backward-compat aliases kept for any external callers
def nutrient_match_to_result(ingredient, vm):
    return match_to_result(ingredient, vm, category='nutrient',
                           source_module='nutrient_recognizer', match_status='exact')

vitamin_match_to_result = nutrient_match_to_result


# ── Plant / organism qualifier prefixes ────────────────────────────────────────
#
# FOODON:03430150 = "naturally shaped whole form (sensu food)"
# FOODON:00005746 = "wild harvested organism material"

_WHOLE_FORM_COMPONENT = ComponentTerm(
    category = 'characteristic',
    label    = 'naturally shaped whole form (sensu food)',
    id       = 'FOODON:03430150',
    iri      = curie_to_iri('FOODON:03430150'),
)

_WILD_HARVESTED_COMPONENT = ComponentTerm(
    category = 'characteristic',
    label    = 'wild harvested organism material',
    id       = 'FOODON:00005746',
    iri      = curie_to_iri('FOODON:00005746'),
)

_WHOLE_PREFIX_RE = re.compile(r'^whole\s+', re.IGNORECASE)
_WILD_PREFIX_RE  = re.compile(r'^wild\s+',  re.IGNORECASE)


def _strip_plant_prefixes(text: str):
    """
    Strip 'wild' and/or 'whole' leading qualifiers from *text* in any order.
    Returns (has_wild, has_whole, plant_text).
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
    """Run steps 1–15 (material recognisers only). Returns MatchResult or None."""
    has_wild, has_whole, plant_text = _strip_plant_prefixes(ingredient)

    # 1. Nutrient
    vm = recognize_nutrient(ingredient)
    if vm:
        return match_to_result(ingredient, vm, category='nutrient',
                               source_module='nutrient_recognizer', match_status='exact')

    # 2. Sweetener
    sm = recognize('sweetener', ingredient)
    if sm:
        return match_to_result(ingredient, sm, category='nutrient', source_module='sweetener')

    # 3. Nut
    ntm = recognize('nut', plant_text if (has_wild or has_whole) else ingredient)
    if ntm:
        result = match_to_result(ingredient, ntm, category='food', source_module='nut')
        if has_wild:
            result.component_terms.insert(0, _WILD_HARVESTED_COMPONENT)
        return result

    # 4. Legume
    lgm = recognize('legume', plant_text if (has_wild or has_whole) else ingredient)
    if lgm:
        result = match_to_result(ingredient, lgm, category='food', source_module='legume')
        if has_wild:
            result.component_terms.insert(0, _WILD_HARVESTED_COMPONENT)
        return result

    # 5. Fruit
    fm = recognize('fruit', plant_text if (has_wild or has_whole) else ingredient)
    if fm:
        result = match_to_result(ingredient, fm, category='food', source_module='fruit')
        if has_whole:
            result.component_terms.insert(0, _WHOLE_FORM_COMPONENT)
        if has_wild:
            result.component_terms.insert(0, _WILD_HARVESTED_COMPONENT)
        return result

    # 6. Root vegetable
    rvm = recognize('root_vegetable', plant_text if (has_wild or has_whole) else ingredient)
    if rvm:
        result = match_to_result(ingredient, rvm, category='food', source_module='root_vegetable')
        if has_wild:
            result.component_terms.insert(0, _WILD_HARVESTED_COMPONENT)
        return result

    # 7. Dairy
    dm = recognize('dairy', ingredient)
    if dm:
        return match_to_result(ingredient, dm, category='dairy', source_module='dairy')

    # 8. Spice
    spm = recognize('spice', plant_text if (has_wild or has_whole) else ingredient)
    if spm:
        result = match_to_result(ingredient, spm, category='spice', source_module='spice')
        if has_wild:
            result.component_terms.insert(0, _WILD_HARVESTED_COMPONENT)
        return result

    # 9. Herb
    hm = recognize('herb', plant_text if (has_wild or has_whole) else ingredient)
    if hm:
        result = match_to_result(ingredient, hm, category='food', source_module='herb')
        if has_wild:
            result.component_terms.insert(0, _WILD_HARVESTED_COMPONENT)
        return result

    # 10. Seed
    sem = recognize('seed', plant_text if (has_wild or has_whole) else ingredient)
    if sem:
        result = match_to_result(ingredient, sem, category='food', source_module='seed')
        if has_wild:
            result.component_terms.insert(0, _WILD_HARVESTED_COMPONENT)
        return result

    # 11. Grain
    gm = recognize('grain', ingredient)
    if gm:
        return match_to_result(ingredient, gm, category='food', source_module='grain')

    # 12. Animal material
    anm = recognize('animal', ingredient)
    if anm:
        return match_to_result(ingredient, anm, category='food', source_module='animal')

    # 13. Oil and fat
    ofm = recognize('lipid', ingredient)
    if ofm:
        return match_to_result(ingredient, ofm, category='food', source_module='lipid')

    # 14. Chemical
    am = recognize('chemical', ingredient)
    if am:
        return match_to_result(ingredient, am, category='additive', source_module='chemical')

    # 15. Fermentation
    fm2 = recognize('fermentation', ingredient)
    if fm2:
        return match_to_result(ingredient, fm2, category='food', source_module='fermentation')

    return None


def _char_prefix_match(text: str):
    """
    Find the longest characteristic key that is a **prefix** of the normalised *text*.
    Returns (curie, label, residual_after_prefix) or None.

    Prefix-only matching prevents a characteristic word buried inside a complex term
    (e.g. a species name) from being erroneously consumed.
    """
    normed = normalize(text)
    best_len, best_key = 0, None
    for key in _CHAR_LOOKUP:
        if normed == key or normed.startswith(key + ' '):
            if len(key) > best_len:
                best_len, best_key = len(key), key
    if best_key is None:
        return None
    curie, label = _CHAR_LOOKUP[best_key]
    residual = normed[len(best_key):].strip()
    return curie, label, residual


def _resolve_residuals(result: MatchResult) -> MatchResult:
    """
    Post-process a MatchResult: scan each unmatched term for characteristic,
    taxonomy, and anatomy terms using the same lookup approach for all three.

    Characteristic matching uses prefix-only matching (front of the term must be a
    complete characteristic key), then the remainder is checked for taxonomy then
    anatomy.  Taxonomy-only and anatomy-only terms are matched against the full
    (normalised) text in that order.

    - Characteristic prefix → ComponentTerm appended (category='characteristic')
    - First taxonomy hit    → written to result.taxonomy
    - First anatomy hit     → written to result.anatomy
    - Fully-resolved terms are removed from unmatched_terms.
    - Status upgrades from 'partial' to 'composite' when all terms are resolved.
    """
    if not result.unmatched_terms:
        return result
    remaining = []
    for ut in result.unmatched_terms:
        resolved = False

        # 1. Try characteristic as a prefix of the unmatched text.
        char_hit = _char_prefix_match(ut.text)
        if char_hit:
            char_curie, char_label, char_residual = char_hit
            result.component_terms.append(ComponentTerm(
                category='characteristic',
                label=char_label,
                id=char_curie,
                iri=curie_to_iri(char_curie),
            ))
            # Check the remainder for a taxonomy term, then anatomy.
            if char_residual and not result.taxonomy:
                k  = normalize(char_residual)
                ks = k[:-1] if k.endswith('s') else None
                tax_curie = _TAXONOMY_LOOKUP.get(k) or (ks and _TAXONOMY_LOOKUP.get(ks))
                if tax_curie:
                    result.taxonomy = tax_curie
                    char_residual = ''
            if char_residual and not result.anatomy:
                k  = normalize(char_residual)
                ks = k[:-1] if k.endswith('s') else None
                anat_curie = _ANATOMY_LOOKUP.get(k) or (ks and _ANATOMY_LOOKUP.get(ks))
                if anat_curie:
                    result.anatomy = anat_curie
                    char_residual = ''
            if char_residual:
                remaining.append(UnmatchedTerm('unresolved', char_residual))
            resolved = True

        # 2. Try full-text taxonomy lookup (first hit only; case-insensitive).
        elif not result.taxonomy:
            k  = normalize(ut.text)
            ks = k[:-1] if k.endswith('s') else None
            tax_curie = _TAXONOMY_LOOKUP.get(k) or (ks and _TAXONOMY_LOOKUP.get(ks))
            if tax_curie:
                result.taxonomy = tax_curie
                resolved = True

        # 3. Try full-text anatomy lookup.
        elif not result.anatomy:
            k  = normalize(ut.text)
            ks = k[:-1] if k.endswith('s') else None
            anat_curie = _ANATOMY_LOOKUP.get(k) or (ks and _ANATOMY_LOOKUP.get(ks))
            if anat_curie:
                result.anatomy = anat_curie
                resolved = True

        if not resolved:
            remaining.append(ut)

    result.unmatched_terms = remaining
    if not result.unmatched_terms and result.match_status == 'partial':
        result.match_status = 'composite'
    return result


def _anatomy_standalone_match(ingredient: str) -> str:
    """Return anatomy CURIE if the full ingredient string maps to a primary anatomy term, else ''."""
    k  = normalize(ingredient)
    ks = k[:-1] if k.endswith('s') else None
    return _ANATOMY_LOOKUP.get(k) or (ks and _ANATOMY_LOOKUP.get(ks)) or ''


def _make_anatomy_result(ingredient: str, anat_curie: str) -> MatchResult:
    """Build a MatchResult for an anatomy-only standalone match."""
    entry = _DB.get(anat_curie, {})
    return MatchResult(
        ingredient=ingredient,
        match_status='exact',
        matched_id=anat_curie,
        matched_label=entry.get('label', ''),
        matched_iri=_anatomy_iri(anat_curie),
        component_terms=[],
        unmatched_terms=[],
        source_module='anatomy_recognizer',
        type='anatomy',
        subtype='',
        anatomy=anat_curie,
    )


def _run_match(ingredient: str, options: argparse.Namespace) -> MatchResult:
    """Internal pipeline — caller must restore result.ingredient if needed."""

    result = _match_material_only(ingredient, options)
    if result:
        result = _resolve_residuals(result)
        # Anatomy standalone match supercedes a composite/partial/parent food match:
        # an exact anatomy term is more specific than a partially-resolved or
        # fallback-inferred food entry.
        if result.match_status in ('composite', 'partial', 'parent'):
            anat_curie = _anatomy_standalone_match(ingredient)
            if anat_curie:
                return _make_anatomy_result(ingredient, anat_curie)
        return result

    # 11. Characteristic — iteratively strip and retry material recognisers
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
                break

            retry = _match_material_only(residual, options)
            if retry:
                retry.ingredient = ingredient
                for ct in reversed(collected_chars):
                    retry.component_terms.insert(0, ct)
                retry.match_status = 'composite' if not retry.unmatched_terms else 'partial'
                result = _resolve_residuals(retry)
                if result.match_status in ('composite', 'partial', 'parent'):
                    anat_curie = _anatomy_standalone_match(ingredient)
                    if anat_curie:
                        return _make_anatomy_result(ingredient, anat_curie)
                return result

            next_cm = recognize_characteristic(residual)
            if not next_cm or next_cm.residual_text == residual:
                break
            current_cm = next_cm

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
            type='characteristic',
            subtype='',
        )

    # TODO: Claude AI general fallback

    # Anatomy standalone match: fallback when all food/characteristic matching fails.
    anat_curie = _anatomy_standalone_match(ingredient)
    if anat_curie:
        return _make_anatomy_result(ingredient, anat_curie)

    return MatchResult(
        ingredient=ingredient,
        match_status='no_match',
        matched_id='',
        matched_label='',
        matched_iri='',
        component_terms=[],
        unmatched_terms=[UnmatchedTerm('unresolved', ingredient)],
        source_module='none',
        type='',
        subtype='',
    )


def match_ingredient(ingredient: str, options: argparse.Namespace) -> MatchResult:
    """Run the recogniser pipeline for one ingredient string.

    Applies assumption rewriting (e.g. "egg" → "chicken egg") before matching,
    then restores the original ingredient text in the result so that reports
    show exactly what the caller supplied.
    """
    original = ingredient
    ingredient = apply_assumptions(ingredient)

    result = _run_match(ingredient, options)

    # Restore the original (pre-assumption) ingredient text for reporting.
    result.ingredient = original
    # Also fix any unmatched term that carries the rewritten text.
    if ingredient != original:
        result.unmatched_terms = [
            UnmatchedTerm(ut.category, original if ut.text == ingredient else ut.text)
            for ut in result.unmatched_terms
        ]
    return result


# ── Notes helpers ──────────────────────────────────────────────────────────────

def _notes_html(matched_id: str) -> str:
    """Return a <details>/<summary> HTML block listing child entries with comments."""
    children = _CHILDREN_BY_PARENT.get(matched_id, [])
    if not children:
        return ''
    items = []
    for child in children:
        link = (
            f'<a href="{_esc(child["iri"])}" target="_blank">{_esc(child["label"])}</a>'
            if child['iri'] else _esc(child['label'])
        )
        items.append(
            f'<li>{link} [{_esc(child["curie"])}] \u2014 {_esc(child["comment"])}</li>'
        )
    inner = '\n'.join(items)
    return f'<details><summary>Notes</summary><ul>\n{inner}\n</ul></details>'


def _notes_md(matched_id: str) -> str:
    """Return a <details>/<summary> markdown block listing child entries with comments."""
    children = _CHILDREN_BY_PARENT.get(matched_id, [])
    if not children:
        return ''
    lines = []
    for child in children:
        link = (
            f'[{child["label"]}]({child["iri"]})'
            if child['iri'] else child['label']
        )
        lines.append(f'{link} [{child["curie"]}] \u2014 {child["comment"]}')
    inner = '<br>'.join(lines)
    return f'<details><summary>Notes</summary>{inner}</details>'


def _notes_tsv(matched_id: str) -> str:
    """Return semicolon-separated note strings for the TSV notes column."""
    children = _CHILDREN_BY_PARENT.get(matched_id, [])
    return '; '.join(f'note:{c["label"]} [{c["curie"]}]' for c in children)


def _fmt_subtype(subtype) -> str:
    """Format subtype for display. Lists become comma-delimited; strings pass through."""
    if isinstance(subtype, list):
        return ', '.join(str(s) for s in subtype)
    return subtype or ''


def _fmt_type(type_str: str, food_terms: list) -> str:
    """Prepend food material category prefix to type, omitting prefix when equal to type."""
    if not food_terms:
        return type_str
    prefix = food_terms[0].category
    if not prefix or prefix == type_str or prefix == 'food':
        return type_str
    # Additives are always chemicals; suppress the redundant ":chemical" suffix.
    if prefix == 'additive' and type_str == 'chemical':
        return 'additive'
    return f'{prefix}:{type_str}'


# ── Report helpers ─────────────────────────────────────────────────────────────

_STATUS_ORDER: dict[str, int] = {
    'exact':     0,
    'composite': 1,
    'parent':    2,
    'partial':   3,
    'no_match':  4,
}


def _sort_results(results: list) -> list:
    """Return results sorted by status (exact → composite → parent → partial → no_match)."""
    return sorted(results, key=lambda r: _STATUS_ORDER.get(r.match_status, 99))


# ── TSV writer ─────────────────────────────────────────────────────────────────

_TSV_FIELDS = [
    'ingredient', 'match_status', 'food_id', 'food_material', 'taxonomy', 'anatomy',
    'type', 'subtype', 'matched_id', 'matched_label',
    'characteristics', 'unmatched_terms', 'source', 'notes',
]


def _split_components(component_terms: list) -> tuple[list, list]:
    """Split component_terms into (food_material_terms, characteristic_terms)."""
    food  = [t for t in component_terms if t.category != 'characteristic']
    chars = [t for t in component_terms if t.category == 'characteristic']
    return food, chars


def write_report_tsv(results: list, options: argparse.Namespace, out_fh) -> None:
    writer = csv.DictWriter(out_fh, fieldnames=_TSV_FIELDS,
                            delimiter='\t', extrasaction='ignore')
    writer.writeheader()
    for r in results:
        child_notes = _notes_tsv(r.matched_id)
        extra_notes = '; '.join(r.notes)
        all_notes   = '; '.join(filter(None, [child_notes, extra_notes]))
        food_terms, char_terms = _split_components(r.component_terms)
        writer.writerow({
            'ingredient':      r.ingredient,
            'match_status':    r.match_status,
            'type':            _fmt_type(r.type, food_terms),
            'subtype':         _fmt_subtype(r.subtype),
            'matched_id':      r.matched_id,
            'matched_label':   r.matched_label,
            'food_id':         '; '.join(t.id for t in food_terms),
            'food_material':   '; '.join(t.label for t in food_terms),
            'taxonomy':        r.taxonomy,
            'anatomy':         r.anatomy,
            'characteristics': '; '.join(t.label for t in char_terms),
            'unmatched_terms': '; '.join(t.text for t in r.unmatched_terms),
            'source':          r.source_module,
            'notes':           all_notes,
        })


# ── Markdown writer ────────────────────────────────────────────────────────────

def _md_cell(text: str) -> str:
    return str(text).replace('|', '\\|').replace('\n', ' ')


def _md_terms(terms: list) -> str:
    return '; '.join(_md_cell(str(t)) for t in terms)


def _md_char_terms(terms: list) -> str:
    """Characteristic terms: linked label, no prefix, no CURIE."""
    parts = []
    for t in terms:
        if t.iri:
            parts.append(f'[{_md_cell(t.label)}]({t.iri})')
        else:
            parts.append(_md_cell(t.label))
    return '; '.join(parts)


def _md_iri_link(id_str: str, iri: str) -> str:
    if iri:
        return f'[{_md_cell(id_str)}]({iri})'
    return _md_cell(id_str)


def _md_food_id(terms: list) -> str:
    """Food material ID column: linked CURIE, no brackets."""
    return '; '.join(
        f'[{_md_cell(t.id)}]({t.iri})' if t.iri else _md_cell(t.id)
        for t in terms
    )


def _md_food_label(terms: list) -> str:
    """Food material label column: linked label, no prefix, no CURIE."""
    return '; '.join(
        f'[{_md_cell(t.label)}]({t.iri})' if t.iri else _md_cell(t.label)
        for t in terms
    )


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
    summary_parts = [f'**{total} ingredient{"s" if total != 1 else ""}**']
    for status in ('exact', 'parent', 'composite', 'partial', 'no_match'):
        n = counts.get(status, 0)
        if n:
            label = status.replace('_', '\\_')
            summary_parts.append(f'{label} \u00d7 {n}')
    out_fh.write(' \u2014 '.join(summary_parts) + '\n\n')
    out_fh.write('## Results\n\n')
    out_fh.write('| Ingredient | Status | ID | Food material | Taxonomy | Anatomy | Type | Subtype | Characteristics | Unmatched Terms | Notes&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |\n')
    out_fh.write('|:-----------|:------:|:---|:--------------|:---------|:--------|:-----|:--------|:----------------|:----------------|:-------------------------|\n')

    _status_label = {
        'exact':     '<span style="color:#1a5c2a;font-weight:700">exact</span>',
        'parent':    'parent',
        'composite': '<span style="color:#28a745;font-weight:700">composite</span>',
        'partial':   'partial',
        'no_match':  '<span style="color:#dc3545;font-weight:700">no\\_match</span>',
    }
    for r in results:
        food_terms, char_terms = _split_components(r.component_terms)
        _tax_iri   = _taxonomy_iri(r.taxonomy)
        _tax_label = _md_cell(_curie_label(r.taxonomy))
        _tax_cell  = (f'[{_tax_label}]({_tax_iri})'
                      if _tax_iri and r.taxonomy else _tax_label)
        _anat_display = _anatomy_ref_curie(r.anatomy)
        _anat_iri   = _anatomy_iri(_anat_display)
        _anat_label = _md_cell(_curie_label(_anat_display))
        _anat_cell  = (f'[{_anat_label}]({_anat_iri})'
                       if _anat_iri and _anat_display else _anat_label)
        out_fh.write(
            f'| {_md_cell(r.ingredient)} '
            f'| {_status_label.get(r.match_status, r.match_status)} '
            f'| {_md_food_id(food_terms)} '
            f'| {_md_food_label(food_terms)} '
            f'| {_tax_cell} '
            f'| {_anat_cell} '
            f'| {_md_cell(_fmt_type(r.type, food_terms))} '
            f'| {_md_cell(_fmt_subtype(r.subtype))} '
            f'| {_md_char_terms(char_terms)} '
            f'| {"; ".join(_md_cell(t.text) for t in r.unmatched_terms)} '
            f'| {_notes_md(r.matched_id)} |\n'
        )

    out_fh.write(
        f'\n**{total}** ingredient{"s" if total != 1 else ""} processed: '
        f'**{counts["exact"]} exact**, '
        f'**{counts["parent"]} parent**, '
        f'**{counts.get("composite", 0)} composite**, '
        f'**{counts.get("partial", 0)} partial**, '
        f'**{counts["no_match"]} no\\_match**\n'
    )

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


# ── HTML writer ────────────────────────────────────────────────────────────────

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
.badge-exact      { background: #1a5c2a; color: #fff; }
.badge-parent     { background: #fff3cd; color: #7d5a00; }
.badge-composite  { background: #28a745; color: #fff; }
.badge-partial    { background: #cce5ff; color: #004085; }
.badge-no-match   { background: #dc3545; color: #fff; }
.term { font-family: 'SFMono-Regular', Consolas, monospace; font-size: 0.82em; }
a { color: #1565c0; text-decoration: none; }
a:hover { text-decoration: underline; }
details {
  border: 1px solid #c8e6c9; border-radius: 4px;
  margin: 0.2rem 0; overflow: hidden;
}
summary {
  padding: 0.3rem 0.6rem; cursor: pointer;
  background: #f1f8e9; font-weight: 600; color: #2c5f2e;
  list-style: none; display: flex; align-items: center; gap: 0.4rem;
  font-size: 0.82em;
}
summary::before { content: '\u25b6'; font-size: 0.65em; transition: transform 0.15s; }
details[open] summary::before { transform: rotate(90deg); }
summary:hover { background: #e8f5e9; }
details[open] summary { border-bottom: 1px solid #c8e6c9; }
details ul { margin: 0.4rem 0.6rem; padding-left: 1rem; font-size: 0.82em; }
details ul li { margin: 0.2rem 0; }
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


def _html_char(term: ComponentTerm) -> str:
    """Characteristic term: linked label, no prefix, no CURIE."""
    if term.iri:
        return f'<span class="term"><a href="{_esc(term.iri)}" target="_blank">{_esc(term.label)}</a></span>'
    return f'<span class="term">{_esc(term.label)}</span>'


def _html_food_id(terms: list) -> str:
    """Food material ID column: linked CURIE, no brackets."""
    parts = []
    for t in terms:
        if t.iri:
            parts.append(f'<span class="term"><a href="{_esc(t.iri)}" target="_blank">{_esc(t.id)}</a></span>')
        else:
            parts.append(f'<span class="term">{_esc(t.id)}</span>')
    return '<br>'.join(parts)


def _html_food_label(terms: list) -> str:
    """Food material label column: linked label, no prefix, no CURIE."""
    parts = []
    for t in terms:
        if t.iri:
            parts.append(f'<span class="term"><a href="{_esc(t.iri)}" target="_blank">{_esc(t.label)}</a></span>')
        else:
            parts.append(f'<span class="term">{_esc(t.label)}</span>')
    return '<br>'.join(parts)


def write_report_html(results: list, options: argparse.Namespace, out_fh) -> None:
    counts: dict[str, int] = {'exact': 0, 'parent': 0, 'composite': 0, 'partial': 0, 'no_match': 0}
    for r in results:
        counts[r.match_status] = counts.get(r.match_status, 0) + 1
    total = len(results)
    today = date.today().isoformat()

    p = []

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

    p.append('<div class="summary-bar">\n')
    p.append(f'  <span class="total">{total} ingredient{"s" if total != 1 else ""}</span>\n')
    for status in ('exact', 'parent', 'composite', 'partial', 'no_match'):
        n = counts.get(status, 0)
        if n:
            p.append(f'  {_badge(status)} &times; {n}\n')
    p.append('</div>\n')

    p.append('<h2>Results</h2>\n<table>\n')
    p.append('  <thead><tr>'
             '<th>Ingredient</th><th>Status</th>'
             '<th>ID</th><th>Food material</th>'
             '<th>Taxonomy</th><th>Anatomy</th>'
             '<th>Type</th><th>Subtype</th>'
             '<th>Characteristics</th>'
             '<th>Unmatched Terms</th><th style="min-width:30ch">Notes</th>'
             '</tr></thead>\n  <tbody>\n')
    for r in results:
        food_terms, char_terms = _split_components(r.component_terms)
        food_id_html   = _html_food_id(food_terms)
        food_html      = _html_food_label(food_terms)
        char_html      = '<br>'.join(_html_char(t) for t in char_terms)
        unmatched_html = '<br>'.join(
            f'<span class="term">{_esc(t.text)}</span>' for t in r.unmatched_terms
        )
        notes_html  = _notes_html(r.matched_id)
        _tax_iri    = _taxonomy_iri(r.taxonomy)
        _tax_lbl    = _esc(_curie_label(r.taxonomy))
        tax_html    = (f'<span class="term"><a href="{_esc(_tax_iri)}" target="_blank">'
                       f'{_tax_lbl}</a></span>'
                       if _tax_iri and r.taxonomy else _tax_lbl)
        _anat_display = _anatomy_ref_curie(r.anatomy)
        _anat_iri   = _anatomy_iri(_anat_display)
        _anat_lbl   = _esc(_curie_label(_anat_display))
        anat_html   = (f'<span class="term"><a href="{_esc(_anat_iri)}" target="_blank">'
                       f'{_anat_lbl}</a></span>'
                       if _anat_iri and _anat_display else _anat_lbl)
        p.append(
            f'    <tr>'
            f'<td>{_esc(r.ingredient)}</td>'
            f'<td>{_badge(r.match_status)}</td>'
            f'<td>{food_id_html}</td>'
            f'<td>{food_html}</td>'
            f'<td>{tax_html}</td>'
            f'<td>{anat_html}</td>'
            f'<td>{_esc(_fmt_type(r.type, food_terms))}</td>'
            f'<td>{_esc(_fmt_subtype(r.subtype))}</td>'
            f'<td>{char_html}</td>'
            f'<td>{unmatched_html}</td>'
            f'<td>{notes_html}</td>'
            f'</tr>\n'
        )
    p.append('  </tbody>\n</table>\n')

    unmatched_results = [r for r in results if r.match_status == 'no_match']
    if unmatched_results:
        n = len(unmatched_results)
        p.append('<h2>Unmatched Ingredients</h2>\n')
        p.append(f'<p><strong>{n}</strong> ingredient{"s" if n != 1 else ""} '
                 f'could not be mapped to any FoodOn term.</p>\n')
        p.append('<ul class="unmatched">\n')
        for r in unmatched_results:
            p.append(f'  <li>{_esc(r.ingredient)}</li>\n')
        p.append('</ul>\n')

    p.append('</body>\n</html>\n')
    out_fh.write(''.join(p))


# ── Input loader ───────────────────────────────────────────────────────────────

def load_input(options: argparse.Namespace) -> list[dict]:
    """Load rows from a local CSV/TSV file or a URL. Returns list of row dicts."""
    src = options.input
    if src.startswith('http://') or src.startswith('https://'):
        try:
            import urllib.request
            with urllib.request.urlopen(src) as resp:
                content = resp.read().decode('utf-8')
            fh  = io.StringIO(content)
            sep = ','
        except Exception as exc:
            print(f'ERROR: could not fetch URL: {exc}', file=sys.stderr)
            sys.exit(1)
    else:
        sep = ',' if src.endswith('.csv') else '\t'
        fh  = open(src, newline='', encoding='utf-8')

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


# ── Main pipeline ──────────────────────────────────────────────────────────────

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

    results = _sort_results(results)

    _VALID_FMTS = {'tsv', 'markdown', 'html'}
    _DEFAULT_EXT = {'tsv': '.tsv', 'markdown': '.md', 'html': '.html'}
    _DEFAULT_NAME = {'tsv': 'report.tsv', 'markdown': 'report.md', 'html': 'report.html'}

    formats = [f.strip() for f in options.fmt.split(',')] if options.fmt else ['tsv']
    unknown = [f for f in formats if f not in _VALID_FMTS]
    if unknown:
        print(f'Unknown format(s): {", ".join(unknown)}. '
              f'Valid: {", ".join(sorted(_VALID_FMTS))}', file=sys.stderr)
        sys.exit(1)

    single = len(formats) == 1

    for fmt in formats:
        if fmt == 'tsv':
            if single and not options.output:
                write_report_tsv(results, options, sys.stdout)
            else:
                out_path = (options.output if single else None) or _DEFAULT_NAME['tsv']
                with open(out_path, 'w', newline='', encoding='utf-8') as out_fh:
                    write_report_tsv(results, options, out_fh)
                print(f'Report written to {out_path}', file=sys.stderr)

        elif fmt == 'markdown':
            out_path = (options.output if single else None) or _DEFAULT_NAME['markdown']
            with open(out_path, 'w', encoding='utf-8') as out_fh:
                write_report_md(results, options, out_fh)
            print(f'Report written to {out_path}', file=sys.stderr)

        elif fmt == 'html':
            out_path = (options.output if single else None) or _DEFAULT_NAME['html']
            with open(out_path, 'w', encoding='utf-8') as out_fh:
                write_report_html(results, options, out_fh)
            print(f'Report written to {out_path}', file=sys.stderr)


# ── Type-test mode ─────────────────────────────────────────────────────────────

def run_type_test(options: argparse.Namespace) -> None:
    """
    Type-test mode: test a single ingredient type against a string or TSV file.

    Usage:
        python3 ingredients.py --type fruit "fresh strawberry"
        python3 ingredients.py --type grain --tsv example.tsv --column ingredient
        python3 ingredients.py --type nutrient "vitamin C"
        python3 ingredients.py --type characteristic "frozen"
    """
    type_name = options.type

    def _recognise(text: str):
        if type_name in ('nutrient', 'vitamin'):
            return recognize_nutrient(text)
        if type_name == 'characteristic':
            return recognize_characteristic(text)
        return recognize(type_name, text)

    def _show(text: str, m) -> None:
        if m:
            extra = (f' → form: {m.matched_label} [{m.matched_id}]'
                     if m.matched_id != m.id else '')
            res   = f'\n  residual: "{m.residual_text}"' if m.residual_text else ''
            print(f'{m.label} [{m.id}] ({m.subtype}){extra}  (via {m.match_type}){res}')
        else:
            print(f'No {type_name!r} recognised in: {text!r}')

    if options.ingredient:
        _show(options.ingredient, _recognise(options.ingredient))
    elif options.tsv:
        sep = '\t' if options.sep in ('\t', '\\t') else options.sep
        col = options.column
        with open(options.tsv, newline='', encoding='utf-8') as fh:
            reader = csv.DictReader(fh, delimiter=sep)
            if col not in (reader.fieldnames or []):
                print(f'ERROR: column "{col}" not found. '
                      f'Available: {reader.fieldnames}', file=sys.stderr)
                sys.exit(1)
            for row in reader:
                ing = row[col]
                _show(ing, _recognise(ing))
    else:
        print(
            'ERROR: with --type, provide either a positional ingredient string '
            'or --tsv FILE.',
            file=sys.stderr,
        )
        sys.exit(1)


def main() -> None:
    options = init_parser()
    if options.fresh:
        print('Freshening')
        # Note: all ROBOT boolean flags require an explicit true/false argument.
        # .owl output preserves xmlns: prefixes (robot issue with other formats).
        subprocess.check_output([
            'robot', 'merge', '--input', '../foodon-edit.ofn',
            'reason', '--reasoner', 'ELK', '--exclude-duplicate-axioms', 'true',
            'relax', '--include-subclass-of', 'true',
            '--output', options.owl,
        ])
    if options.synonyms != 'ok':
        _apply_synonyms_mode(options.synonyms)
    if options.type:
        run_type_test(options)
    elif options.input:
        run_pipeline(options)
    elif options.rebuild:
        types = (list(_RAW['configuration'].keys())
                 if options.rebuild == 'all'
                 else [options.rebuild])
        for t in types:
            if t == 'taxonomy':
                build_taxonomy(options.owl, dry_run=options.dry_run)
            elif t == 'anatomy':
                build_anatomy(options.owl, dry_run=options.dry_run)
            else:
                build_refresh(t, options.owl, dry_run=options.dry_run)
    else:
        print(
            'ERROR: either -i/--input (pipeline mode), --type TYPE (type-test mode), '
            'or --rebuild TYPE|all (rebuild mode) is required.',
            file=sys.stderr,
        )
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# § 8  Build / refresh
# ══════════════════════════════════════════════════════════════════════════════


def _collect_owl_terms(roots: list, exclude_curies: dict, owl_path: str) -> dict:
    """
    BFS from each root CURIE in *owl_path*; return {curie: {label, synonyms, iri}}
    for all non-deprecated descendants, excluding subtrees rooted at any excluded CURIE.

    Collects hasExactSynonym and hasSynonym annotations; related and narrow synonyms
    are excluded.
    """
    try:
        from rdflib import Graph, URIRef, RDFS
        from collections import deque
    except ImportError:
        print('ERROR: rdflib is required for --rebuild. Install with: pip install rdflib',
              file=sys.stderr)
        return {}

    DEPRECATED = URIRef('http://www.w3.org/2002/07/owl#deprecated')
    EXACT_SYN  = URIRef('http://www.geneontology.org/formats/oboInOwl#hasExactSynonym')
    HAS_SYN    = URIRef('http://www.geneontology.org/formats/oboInOwl#hasSynonym')

    import logging
    _log  = logging.getLogger('rdflib.term')
    _prev = _log.level
    _log.setLevel(logging.ERROR)
    g = Graph()
    try:
        g.parse(owl_path)
    finally:
        _log.setLevel(_prev)

    # Build child index: parent_iri → [child_iri, ...]
    children: dict[str, list[str]] = {}
    for s, _, o in g.triples((None, RDFS.subClassOf, None)):
        children.setdefault(str(o), []).append(str(s))

    def dep(iri: str) -> bool:
        return (URIRef(iri), DEPRECATED, None) in g

    # Expand excluded subtrees (BFS from each exclude root)
    exclude_iris: set[str] = set()
    for excl_curie in (exclude_curies or {}):
        excl_iri = curie_to_iri(excl_curie)
        q: deque[str] = deque([excl_iri])
        while q:
            node = q.popleft()
            if node in exclude_iris:
                continue
            exclude_iris.add(node)
            for ch in children.get(node, []):
                q.append(ch)

    # BFS from each root (roots themselves are not added as results, only descendants)
    result: dict[str, dict] = {}
    visited: set[str] = set(exclude_iris)
    queue: deque[str] = deque()
    for root_curie in roots:
        root_iri = curie_to_iri(root_curie)
        if root_iri not in visited:
            visited.add(root_iri)
            queue.append(root_iri)

    while queue:
        node_iri = queue.popleft()
        if dep(node_iri):
            continue
        label = str(next(g.objects(URIRef(node_iri), RDFS.label), ''))
        if label:
            curie = _char_short(node_iri)
            node_ref = URIRef(node_iri)
            seen_syns: set[str] = set()
            syns: list[str] = []
            for prop in (EXACT_SYN, HAS_SYN):
                for s in g.objects(node_ref, prop):
                    v = str(s)
                    if v not in seen_syns:
                        seen_syns.add(v)
                        syns.append(v)
            result[curie] = {'label': label, 'synonyms': syns, 'iri': node_iri}
        for child_iri in children.get(node_iri, []):
            if child_iri not in visited:
                visited.add(child_iri)
                queue.append(child_iri)

    return result


def build_refresh(type_name: str, owl_path: str, dry_run: bool = False) -> None:
    """
    Rebuild/refresh ingredients.yaml entries for one configuration type.

    Compares OWL descendants of the type's configured roots against existing YAML
    entries, then prints a report of:
      - New terms (in OWL but not in YAML)
      - Possibly deprecated terms (in YAML but not in OWL)
      - Existing entries with new OWL synonyms not yet in the synonyms list

    With dry_run=False, writes additions/updates back to ingredients.yaml using
    ruamel.yaml to preserve existing formatting.
    """
    cfg = _CONFIG.get(type_name)
    if cfg is None:
        print(f'ERROR: unknown type {type_name!r}. '
              f'Known types: {list(_CONFIG.keys())}', file=sys.stderr)
        return

    roots        = cfg.get('roots') or []
    owl_basename = os.path.basename(owl_path)
    print(f'=== Build/Refresh: {type_name} | OWL: {owl_basename} ===\n')

    if not roots:
        print(f'  (no OWL roots configured — skipping)\n')
        return

    if not os.path.exists(owl_path):
        print(f'ERROR: OWL file not found: {owl_path}', file=sys.stderr)
        return

    exclude   = cfg.get('exclude') or {}
    owl_terms = _collect_owl_terms(roots, exclude, owl_path)

    # ── Anatomy-specific: cross-reference in_taxon links ─────────────────────
    # For each anatomy term in the OWL, check for an 'in taxon' (RO:0002162)
    # restriction.  If found, record the taxon CURIE and add any missing taxonomy
    # entries to ingredients.yaml.
    taxon_xrefs:          dict[str, str]  = {}  # anatomy_curie → taxon_curie
    new_taxonomy_entries: dict[str, dict] = {}  # taxon_curie → {label} (not yet in DB)

    if type_name == 'anatomy' and owl_terms:
        try:
            from rdflib import Graph as _Graph, URIRef as _URIRef, BNode as _BNode
            from rdflib import RDFS as _RDFS
        except ImportError:
            pass
        else:
            _IN_TAXON = _URIRef('http://purl.obolibrary.org/obo/RO_0002162')
            _ON_PROP  = _URIRef('http://www.w3.org/2002/07/owl#onProperty')
            _SOME_VAL = _URIRef('http://www.w3.org/2002/07/owl#someValuesFrom')
            import logging as _logging
            _xlog  = _logging.getLogger('rdflib.term')
            _xprev = _xlog.level
            _xlog.setLevel(_logging.ERROR)
            _gx = _Graph()
            try:
                _gx.parse(owl_path)
            finally:
                _xlog.setLevel(_xprev)
            for _anat_curie, _info in owl_terms.items():
                _node = _URIRef(_info['iri'])
                for _restr in _gx.objects(_node, _RDFS.subClassOf):
                    if isinstance(_restr, _BNode) and (_restr, _ON_PROP, _IN_TAXON) in _gx:
                        for _taxon in _gx.objects(_restr, _SOME_VAL):
                            if not isinstance(_taxon, _BNode):
                                _tax_curie = _taxonomy_curie(str(_taxon))
                                taxon_xrefs[_anat_curie] = _tax_curie
                                if _tax_curie not in _DB:
                                    _lab = next(
                                        (str(_l) for _l in _gx.objects(_taxon, _RDFS.label)),
                                        _tax_curie,
                                    )
                                    new_taxonomy_entries[_tax_curie] = {'label': _lab}
                                break
                    if _anat_curie in taxon_xrefs:
                        break

    yaml_entries    = get_entries_by_type(type_name)
    owl_keys        = set(owl_terms.keys())
    yaml_keys       = set(yaml_entries.keys())
    new_curies      = sorted(owl_keys - yaml_keys)
    missing_curies  = sorted(yaml_keys - owl_keys)
    existing_curies = sorted(owl_keys & yaml_keys)

    # Compute synonym updates and migrations for each existing entry.
    #
    # updates:  curie → full OWL synonym list that replaces the YAML synonyms field
    # migrates: curie → list of current YAML synonyms not in OWL (i.e. not ontology-
    #           sourced) that will be moved to ai_synonyms[status=proposed].
    #           Synonyms already present in ai_synonyms are skipped.
    updates:  dict[str, list[str]] = {}
    migrates: dict[str, list[str]] = {}
    for curie in existing_curies:
        owl_info   = owl_terms[curie]
        yaml_entry = yaml_entries[curie]
        yaml_syns      = list(yaml_entry.get('synonyms') or [])
        owl_syns       = list(owl_info['synonyms'])
        yaml_syns_norm = {s.lower() for s in yaml_syns}
        owl_syns_norm  = {s.lower() for s in owl_syns}
        existing_ai    = {s.lower() for s in (yaml_entry.get('ai_synonyms') or {})}

        # Synonyms in YAML but absent from OWL — migrate to ai_synonyms
        orphaned = [s for s in yaml_syns
                    if s.lower() not in owl_syns_norm
                    and s.lower() not in existing_ai]
        if orphaned:
            migrates[curie] = orphaned

        # OWL synonym list replaces YAML whenever they differ
        if owl_syns_norm != yaml_syns_norm:
            updates[curie] = owl_syns

    # ── Report ───────────────────────────────────────────────────────────────
    # Split new_curies: genuinely absent vs already owned by another primary type
    truly_new     = [c for c in new_curies if not _DB.get(c)]
    type_extended = [c for c in new_curies if _DB.get(c)]

    print(f'[NEW - in OWL under roots, not in ingredients.yaml]  ({len(truly_new)} terms)')
    for curie in truly_new:
        info = owl_terms[curie]
        print(f'  + {curie}  {info["label"]}')
        if info['synonyms']:
            print(f'    ontology synonyms: {info["synonyms"]}')

    if type_extended:
        print(f'\n[EXTENDS EXISTING - will add {type_name!r} as secondary type]  ({len(type_extended)} terms)')
        for curie in type_extended:
            info          = owl_terms[curie]
            existing_prim = _primary_type(_DB[curie])
            print(f'  ~ {curie}  {info["label"]}  (primary: {existing_prim!r})')

    print(f'\n[POSSIBLY DEPRECATED - in ingredients.yaml but not in OWL under roots]  ({len(missing_curies)} terms)')
    for curie in missing_curies:
        entry = yaml_entries[curie]
        print(f'  ! {curie}  {entry.get("label", "")}')

    print(f'\n[OWL SYNONYM UPDATES for existing entries]  ({len(updates)} terms)')
    for curie, owl_syns in sorted(updates.items()):
        label    = yaml_entries[curie].get('label', '')
        old_syns = list(yaml_entries[curie].get('synonyms') or [])
        print(f'  ~ {curie}  {label}')
        print(f'    was:  {old_syns}')
        print(f'    now:  {owl_syns}')

    print(f'\n[MIGRATE to ai_synonyms (in YAML but not in OWL)]  ({len(migrates)} terms)')
    for curie, orphaned in sorted(migrates.items()):
        label = yaml_entries[curie].get('label', '')
        print(f'  >> {curie}  {label}')
        print(f'     → ai_synonyms[status=proposed]: {orphaned}')

    in_sync = len(existing_curies) - len(set(updates) | set(migrates))
    print(f'\n[IN SYNC]  {in_sync} entries match')

    if taxon_xrefs:
        new_in_tax = [c for c in taxon_xrefs.values() if c in new_taxonomy_entries]
        print(f'\n[TAXONOMY CROSS-REFERENCES from in_taxon]  '
              f'({len(taxon_xrefs)} anatomy terms with taxon link; '
              f'{len(set(new_in_tax))} new taxonomy entries)')
        for tax_curie in sorted(set(new_in_tax)):
            print(f'  + {tax_curie}  {new_taxonomy_entries[tax_curie]["label"]}')

    if dry_run:
        print('\nDry-run mode — no files written.')
        return

    # ── Write ────────────────────────────────────────────────────────────────
    try:
        from ruamel.yaml import YAML as RuamelYAML
    except ImportError:
        print('\nERROR: ruamel.yaml is required for writing. '
              'Install with: pip install ruamel.yaml', file=sys.stderr)
        return

    ry = RuamelYAML()
    ry.preserve_quotes = True
    with open(_YAML, encoding='utf-8') as fh:
        doc = ry.load(fh)

    ingredient_section = doc['ingredient']

    for curie in new_curies:
        info     = owl_terms[curie]
        existing = ingredient_section.get(curie) or {}
        existing_type = existing.get('type', '')

        if existing_type:
            # Already owned by another primary type — extend the type list.
            # Food material types (fruit, dairy, grain …) always win primary position
            # over annotation types (anatomy, taxonomy).
            _FOOD_TYPES = {
                'chemical', 'nutrient', 'sweetener', 'fruit', 'dairy', 'spice',
                'herb', 'seed', 'nut', 'legume', 'grain', 'root_vegetable', 'lipid', 'fermentation',
                'animal',
            }
            existing_prim = (existing_type[0]
                             if isinstance(existing_type, list) else existing_type)
            # Decide type order: food type goes first; anatomy/taxonomy go second.
            if type_name in _FOOD_TYPES and existing_prim not in _FOOD_TYPES:
                # New type is food; existing primary is annotation → food takes primary.
                all_types = ([type_name] + (list(existing_type)
                             if isinstance(existing_type, list) else [existing_type]))
            else:
                all_types = ((list(existing_type)
                             if isinstance(existing_type, list) else [existing_type])
                             + [type_name])
            # Deduplicate while preserving order.
            seen: set = set()
            deduped = [t for t in all_types if not (t in seen or seen.add(t))]
            existing['type'] = deduped[0] if len(deduped) == 1 else deduped
            # Leave label/synonyms intact; the primary type owns them.
        else:
            # Genuinely new entry.
            new_entry: dict = {
                'type':     type_name,
                'label':    info['label'],
                'synonyms': list(info['synonyms']),
            }
            if existing.get('ai_synonyms'):
                new_entry['ai_synonyms'] = existing['ai_synonyms']
            ingredient_section[curie] = new_entry

    for curie, owl_syns in updates.items():
        ingredient_section[curie]['synonyms'] = owl_syns

    for curie, orphaned in migrates.items():
        entry = ingredient_section[curie]
        ai_section = dict(entry.get('ai_synonyms') or {})
        ai_lower   = {k.lower() for k in ai_section}
        for syn in orphaned:
            if syn.lower() not in ai_lower:
                ai_section[syn] = {'status': 'proposed'}
        entry['ai_synonyms'] = ai_section

    # Auto-generate stripped synonyms for anatomy-typed entries whose label ends in
    # an anatomical suffix (seed/grain/kernel/groat).  E.g. "buckwheat seed" gains
    # synonym "buckwheat" so bare ingredient names resolve to the anatomical form.
    _STRIP_SFXS = (' seeds', ' grains', ' kernels', ' groats',
                   ' seed',  ' grain',  ' kernel',  ' groat')
    for curie, entry in ingredient_section.items():
        if not isinstance(entry, dict) or entry.get('parent'):
            continue
        et = entry.get('type', '')
        tl = et if isinstance(et, list) else [et]
        if 'anatomy' not in tl:
            continue
        label = entry.get('label', '')
        base = None
        for sfx in _STRIP_SFXS:
            if label.lower().endswith(sfx):
                base = label[:len(label) - len(sfx)]
                break
        if base is None:
            continue
        bl = base.lower()
        existing_syns = {s.lower() for s in (entry.get('synonyms') or [])}
        existing_ai   = {s.lower() for s in (entry.get('ai_synonyms') or {})}
        if bl not in existing_syns and bl not in existing_ai:
            syns = list(entry.get('synonyms') or [])
            syns.append(base)
            entry['synonyms'] = syns

    # Write anatomy → taxonomy cross-references
    for anat_curie, tax_curie in taxon_xrefs.items():
        if anat_curie in ingredient_section:
            ingredient_section[anat_curie]['taxonomy'] = tax_curie

    # Add any new taxonomy entries discovered via in_taxon links
    for tax_curie, tax_info in new_taxonomy_entries.items():
        if tax_curie not in ingredient_section:
            ingredient_section[tax_curie] = {
                'type':     'taxonomy',
                'label':    tax_info['label'],
                'synonyms': [],
            }

    with open(_YAML, 'w', encoding='utf-8') as fh:
        ry.dump(doc, fh)

    print(f'\nUpdated {_YAML}')


_TAXONOMY_FOOD_TYPES = frozenset([
    'fruit', 'sweetener', 'dairy', 'spice', 'herb', 'seed',
    'nut', 'legume', 'grain', 'root_vegetable', 'lipid', 'fermentation', 'animal',
])

_ANATOMY_FOOD_TYPES = frozenset([
    'fruit', 'sweetener', 'dairy', 'spice', 'herb', 'seed',
    'nut', 'legume', 'grain', 'root_vegetable', 'lipid', 'fermentation', 'animal',
])


def _taxonomy_curie(iri: str) -> str:
    """Convert an organism IRI to a CURIE (OBO → standard CURIE, Wikidata → wd:Q...)."""
    if iri.startswith(OBO):
        return _char_short(iri)
    m = re.match(r'https?://(?:www\.)?wikidata\.org/(?:entity|wiki)/(\w+)', iri)
    if m:
        return f'wd:{m.group(1)}'
    return iri


def _annotate_food_taxonomy(owl_path: str, dry_run: bool = False) -> None:
    """
    Annotate food material entries in ingredients.yaml with 'taxonomy' CURIEs.

    For each entry whose type is in _TAXONOMY_FOOD_TYPES (and has no 'parent' field),
    queries the OWL for an 'in taxon' (RO:0002162) restriction on the entry's IRI.
    If not found directly, the OWL superclass hierarchy is traversed (BFS) until an
    ancestor with an 'in taxon' restriction is found.  The taxon target may be any
    named IRI (NCBITaxon:, FOODON:, wd:, or other OBO prefix).
    Writes the discovered CURIE as a 'taxonomy' attribute on the YAML entry.
    """
    try:
        from rdflib import Graph, URIRef, BNode, RDFS
    except ImportError:
        print('ERROR: rdflib is required. Install with: pip install rdflib', file=sys.stderr)
        return

    IN_TAXON = URIRef('http://purl.obolibrary.org/obo/RO_0002162')
    ON_PROP  = URIRef('http://www.w3.org/2002/07/owl#onProperty')
    SOME_VAL = URIRef('http://www.w3.org/2002/07/owl#someValuesFrom')

    def _get_in_taxon(node_iri: str) -> Optional[str]:
        """Return the IRI of the 'in taxon' target for *node_iri*, or None."""
        node = URIRef(node_iri)
        for restriction in g.objects(node, RDFS.subClassOf):
            if isinstance(restriction, BNode):
                if (restriction, ON_PROP, IN_TAXON) in g:
                    for taxon in g.objects(restriction, SOME_VAL):
                        if not isinstance(taxon, BNode):
                            return str(taxon)
        return None

    owl_basename = os.path.basename(owl_path)
    print(f'\n=== Taxonomy Annotation | OWL: {owl_basename} ===\n')
    print(f'  Food types scanned: {", ".join(sorted(_TAXONOMY_FOOD_TYPES))}\n')

    if not os.path.exists(owl_path):
        print(f'ERROR: OWL file not found: {owl_path}', file=sys.stderr)
        return

    import logging
    _log  = logging.getLogger('rdflib.term')
    _prev = _log.level
    _log.setLevel(logging.ERROR)
    g = Graph()
    try:
        g.parse(owl_path)
    finally:
        _log.setLevel(_prev)

    # Only annotate top-level (non-parent) food material entries
    food_entries = {
        curie: entry
        for curie, entry in _DB.items()
        if _primary_type(entry) in _TAXONOMY_FOOD_TYPES
        and not entry.get('parent')
    }

    updates:  dict[str, str] = {}
    no_taxon: list[str]      = []
    in_sync:  int            = 0

    for curie, entry in sorted(food_entries.items()):
        iri       = curie_to_iri(curie)
        taxon_iri = _get_in_taxon(iri)

        if taxon_iri is None:
            # BFS up OWL superclass hierarchy to find the first ancestor with in taxon.
            # Only follow named classes (IRIs starting with http); skip blank nodes.
            visited: set[str] = {iri}
            queue: list[str]  = []
            for p in g.objects(URIRef(iri), RDFS.subClassOf):
                ps = str(p)
                if ps.startswith('http') and ps not in visited:
                    visited.add(ps)
                    queue.append(ps)
            while queue and taxon_iri is None:
                ancestor = queue.pop(0)
                taxon_iri = _get_in_taxon(ancestor)
                if taxon_iri is None:
                    for p in g.objects(URIRef(ancestor), RDFS.subClassOf):
                        ps = str(p)
                        if ps.startswith('http') and ps not in visited:
                            visited.add(ps)
                            queue.append(ps)

        if taxon_iri is None:
            existing = entry.get('taxonomy', '')
            if existing:
                in_sync += 1   # manually annotated, OWL has no data
            else:
                no_taxon.append(curie)
            continue

        new_curie = _taxonomy_curie(taxon_iri)
        existing  = entry.get('taxonomy', '')
        if existing == new_curie:
            in_sync += 1
        else:
            updates[curie] = new_curie

    # ── Report ───────────────────────────────────────────────────────────────
    print(f'[TAXONOMY UPDATES]  ({len(updates)} terms)')
    for curie in sorted(updates):
        label   = food_entries[curie].get('label', curie)
        old_val = food_entries[curie].get('taxonomy', '')
        suffix  = f'  (was: {old_val})' if old_val else ''
        print(f'  ~ {curie}  {label} → {updates[curie]}{suffix}')

    print(f'\n[NO IN-TAXON FOUND IN OWL]  {len(no_taxon)} terms')

    print(f'\n[IN SYNC]  {in_sync} entries unchanged')

    if dry_run:
        print('\nDry-run mode — no files written.')
        return

    if not updates:
        print('\nNo updates to write.')
        return

    # ── Write ────────────────────────────────────────────────────────────────
    try:
        from ruamel.yaml import YAML as RuamelYAML
    except ImportError:
        print('\nERROR: ruamel.yaml is required for writing. '
              'Install with: pip install ruamel.yaml', file=sys.stderr)
        return

    ry = RuamelYAML()
    ry.preserve_quotes = True
    with open(_YAML, encoding='utf-8') as fh:
        doc = ry.load(fh)

    ingredient_section = doc['ingredient']
    for curie, tax_curie in updates.items():
        if curie in ingredient_section:
            ingredient_section[curie]['taxonomy'] = tax_curie

    with open(_YAML, 'w', encoding='utf-8') as fh:
        ry.dump(doc, fh)

    print(f'\nUpdated {_YAML}')


def _annotate_taxonomy_common_parts(dry_run: bool = False) -> None:
    """
    Phase 3 of build_taxonomy: populate common_parts on taxonomy entries.

    Builds a reverse map from taxonomy CURIEs to the labels of all food material
    entries that have a matching 'taxonomy' attribute in ingredients.yaml, then
    writes any new common_parts values back to the taxonomy entries.

    Also queries ingredients.yaml for any synonyms of taxonomy entries that could
    serve as common_names (using oboInOwl synonym annotations is a future addition;
    for now, existing YAML synonyms are promoted as candidates).
    """
    print('\n=== Taxonomy Common Parts | ingredients.yaml ===\n')

    # Build reverse map: taxon_curie → [food material label, …]
    parts_map: dict[str, list[str]] = {}
    for curie, entry in _DB.items():
        tax = entry.get('taxonomy', '')
        if not tax or _primary_type(entry) not in _TAXONOMY_FOOD_TYPES:
            continue
        label = entry.get('label', '')
        if label:
            parts_map.setdefault(tax, []).append(label)

    updates: dict[str, list[str]] = {}
    in_sync = 0

    for curie, entry in sorted(_DB.items()):
        if not _has_type(entry, 'taxonomy'):
            continue
        computed = sorted(set(parts_map.get(curie, [])))
        existing = list(entry.get('common_parts') or [])
        new_parts = [p for p in computed if p not in existing]
        if new_parts:
            updates[curie] = existing + new_parts
        else:
            in_sync += 1

    print(f'[COMMON_PARTS UPDATES]  ({len(updates)} taxonomy entries)')
    for curie in sorted(updates):
        label = _DB[curie].get('label', curie)
        print(f'  ~ {curie}  {label}: common_parts → {updates[curie]}')
    print(f'\n[IN SYNC]  {in_sync} entries unchanged')

    if dry_run:
        print('\nDry-run mode — no files written.')
        return
    if not updates:
        print('\nNo updates to write.')
        return

    try:
        from ruamel.yaml import YAML as RuamelYAML
    except ImportError:
        print('\nERROR: ruamel.yaml is required. Install with: pip install ruamel.yaml',
              file=sys.stderr)
        return

    ry = RuamelYAML()
    ry.preserve_quotes = True
    with open(_YAML, encoding='utf-8') as fh:
        doc = ry.load(fh)

    ingredient_section = doc['ingredient']
    for curie, parts in updates.items():
        if curie in ingredient_section:
            ingredient_section[curie]['common_parts'] = parts

    with open(_YAML, 'w', encoding='utf-8') as fh:
        ry.dump(doc, fh)
    print(f'\nUpdated {_YAML}')


def build_taxonomy(owl_path: str, dry_run: bool = False) -> None:
    """
    Rebuild taxonomy entries and annotate food material entries with organism CURIEs.

    Phase 1: refresh taxonomy type entries (organism names from NCBITaxon:2 OWL subtree)
             via the standard build_refresh logic.
    Phase 2: annotate food material entries (fruit, dairy, spice, herb, seed, grain,
             lipid, fermentation, etc.) with 'taxonomy' CURIEs derived from
             'in taxon' (RO:0002162) links in the OWL.
    Phase 3: populate common_parts on taxonomy entries from YAML back-links.
    """
    build_refresh('taxonomy', owl_path, dry_run=dry_run)
    _annotate_food_taxonomy(owl_path, dry_run=dry_run)
    _annotate_taxonomy_common_parts(dry_run=dry_run)


def _annotate_food_anatomy(owl_path: str, dry_run: bool = False) -> None:
    """
    Annotate food material entries in ingredients.yaml with 'anatomy' CURIEs.

    For each entry whose type is in _ANATOMY_FOOD_TYPES (and has no 'parent' field),
    traverses rdfs:subClassOf upward from the food material's IRI until the first
    named UBERON, PO, or FAO class is encountered.  That class becomes the 'anatomy'
    value for the entry.  Writes the discovered CURIE as an 'anatomy' attribute on
    the YAML entry.
    """
    try:
        from rdflib import Graph, URIRef, RDFS
    except ImportError:
        print('ERROR: rdflib is required. Install with: pip install rdflib', file=sys.stderr)
        return

    _EXT_IRI_PREFIXES = (
        'http://purl.obolibrary.org/obo/UBERON_',
        'http://purl.obolibrary.org/obo/PO_',
        'http://purl.obolibrary.org/obo/FAO_',
    )

    def _nearest_ext_ancestor(start_iri: str) -> str:
        """BFS up rdfs:subClassOf; return first UBERON/PO/FAO named class IRI."""
        visited: set[str] = {start_iri}
        queue: list[str]  = [start_iri]
        while queue:
            node_iri = queue.pop(0)
            for parent in g.objects(URIRef(node_iri), RDFS.subClassOf):
                ps = str(parent)
                if ps in visited or not ps.startswith('http'):
                    continue
                visited.add(ps)
                if any(ps.startswith(p) for p in _EXT_IRI_PREFIXES):
                    return ps
                queue.append(ps)
        return ''

    owl_basename = os.path.basename(owl_path)
    print(f'\n=== Anatomy Annotation | OWL: {owl_basename} ===\n')
    print(f'  Food types scanned: {", ".join(sorted(_ANATOMY_FOOD_TYPES))}\n')

    if not os.path.exists(owl_path):
        print(f'ERROR: OWL file not found: {owl_path}', file=sys.stderr)
        return

    import logging
    _log  = logging.getLogger('rdflib.term')
    _prev = _log.level
    _log.setLevel(logging.ERROR)
    g = Graph()
    try:
        g.parse(owl_path)
    finally:
        _log.setLevel(_prev)

    food_entries = {
        curie: entry
        for curie, entry in _DB.items()
        if _primary_type(entry) in _ANATOMY_FOOD_TYPES
        and not entry.get('parent')
    }

    updates:   dict[str, str] = {}
    no_anat:   int            = 0
    in_sync:   int            = 0

    for curie, entry in sorted(food_entries.items()):
        iri = curie_to_iri(curie)
        # If the food material's own IRI is a UBERON/PO/FAO entity it IS the anatomy.
        if any(iri.startswith(p) for p in _EXT_IRI_PREFIXES):
            anat_iri = iri
        else:
            anat_iri = _nearest_ext_ancestor(iri)

        if not anat_iri:
            existing = entry.get('anatomy', '')
            if existing:
                in_sync += 1
            else:
                no_anat += 1
            continue

        new_curie = _char_short(anat_iri)
        existing  = entry.get('anatomy', '')
        if existing == new_curie:
            in_sync += 1
        else:
            updates[curie] = new_curie

    print(f'[ANATOMY UPDATES]  ({len(updates)} terms)')
    for curie in sorted(updates):
        label   = food_entries[curie].get('label', curie)
        old_val = food_entries[curie].get('anatomy', '')
        suffix  = f'  (was: {old_val})' if old_val else ''
        print(f'  ~ {curie}  {label} → {updates[curie]}{suffix}')

    print(f'\n[NO ANATOMY RELATION FOUND IN OWL]  {no_anat} terms')
    print(f'\n[IN SYNC]  {in_sync} entries unchanged')

    if dry_run:
        print('\nDry-run mode — no files written.')
        return
    if not updates:
        print('\nNo updates to write.')
        return

    try:
        from ruamel.yaml import YAML as RuamelYAML
    except ImportError:
        print('\nERROR: ruamel.yaml is required. Install with: pip install ruamel.yaml',
              file=sys.stderr)
        return

    ry = RuamelYAML()
    ry.preserve_quotes = True
    with open(_YAML, encoding='utf-8') as fh:
        doc = ry.load(fh)

    ingredient_section = doc['ingredient']
    for curie, anat_curie in updates.items():
        if curie in ingredient_section:
            ingredient_section[curie]['anatomy'] = anat_curie

    with open(_YAML, 'w', encoding='utf-8') as fh:
        ry.dump(doc, fh)
    print(f'\nUpdated {_YAML}')


def _annotate_anatomy_refs(owl_path: str, dry_run: bool = False) -> None:
    """
    Phase 3 of build_anatomy(): for every anatomy entry in the YAML that is a
    FOODON-prefixed term, traverse rdfs:subClassOf upward in the merged OWL until
    the nearest UBERON, PO, or FAO ancestor is found.  Write it as 'anatomy_ref'
    on the anatomy entry so the report layer can display an external-ontology link.

    UBERON/PO/FAO anatomy entries are skipped (they already ARE the external ref).
    Manually set 'anatomy_ref' values are preserved when no OWL ancestor is found.
    """
    try:
        from rdflib import Graph, URIRef, RDFS
    except ImportError:
        print('ERROR: rdflib is required. Install with: pip install rdflib', file=sys.stderr)
        return

    owl_basename = os.path.basename(owl_path)
    print(f'\n=== Anatomy External References | OWL: {owl_basename} ===\n')

    if not os.path.exists(owl_path):
        print(f'ERROR: OWL file not found: {owl_path}', file=sys.stderr)
        return

    import logging
    _log  = logging.getLogger('rdflib.term')
    _prev = _log.level
    _log.setLevel(logging.ERROR)
    g = Graph()
    try:
        g.parse(owl_path)
    finally:
        _log.setLevel(_prev)

    # OBO IRI prefixes for the target external ontologies
    _EXT_IRI_PREFIXES = (
        'http://purl.obolibrary.org/obo/UBERON_',
        'http://purl.obolibrary.org/obo/PO_',
        'http://purl.obolibrary.org/obo/FAO_',
    )

    def _nearest_ext_ancestor(start_iri: str) -> str:
        """BFS up rdfs:subClassOf; return first UBERON/PO/FAO IRI (excl. start)."""
        visited: set[str] = {start_iri}
        queue: list[str]  = [start_iri]
        while queue:
            node_iri = queue.pop(0)
            for parent in g.objects(URIRef(node_iri), RDFS.subClassOf):
                ps = str(parent)
                if ps in visited or not ps.startswith('http'):
                    continue
                visited.add(ps)
                if any(ps.startswith(p) for p in _EXT_IRI_PREFIXES):
                    return ps
                queue.append(ps)
        return ''

    anatomy_entries = {
        curie: entry
        for curie, entry in _DB.items()
        if _primary_type(entry) == 'anatomy'
        and curie.split(':')[0] == 'FOODON'
    }

    updates: dict[str, str] = {}   # curie → new ref curie
    no_ref:  list[str]      = []
    in_sync: list[str]      = []

    for curie, entry in sorted(anatomy_entries.items()):
        iri     = curie_to_iri(curie)
        ref_iri = _nearest_ext_ancestor(iri)
        if not ref_iri:
            no_ref.append(curie)
            continue
        ref_curie = _char_short(ref_iri)
        existing  = entry.get('anatomy_ref', '')
        if existing == ref_curie:
            in_sync.append(curie)
        else:
            updates[curie] = ref_curie

    if updates:
        print(f'[UPDATED]  ({len(updates)} anatomy entries)\n')
        for curie in sorted(updates):
            lbl = _DB.get(curie, {}).get('label', curie)
            old = _DB.get(curie, {}).get('anatomy_ref', '')
            suffix = f'  (was: {old})' if old else ''
            print(f'  ~ {curie}  {lbl} → {updates[curie]}{suffix}')
    if no_ref:
        print(f'\n[NO EXTERNAL ANCESTOR FOUND IN OWL]  ({len(no_ref)} terms — manual anatomy_ref may be set)')
    if in_sync:
        print(f'\n[IN SYNC]  {len(in_sync)} entries already annotated')

    if dry_run:
        print('\nDry-run mode — no files written.')
        return
    if not updates:
        print('\nNo updates to write.')
        return

    try:
        from ruamel.yaml import YAML as RuamelYAML
    except ImportError:
        print('\nERROR: ruamel.yaml is required. Install with: pip install ruamel.yaml',
              file=sys.stderr)
        return

    ry = RuamelYAML()
    ry.preserve_quotes = True
    with open(_YAML, encoding='utf-8') as fh:
        doc = ry.load(fh)

    ingredient_section = doc['ingredient']
    for curie, ref_curie in updates.items():
        if curie in ingredient_section:
            ingredient_section[curie]['anatomy_ref'] = ref_curie

    with open(_YAML, 'w', encoding='utf-8') as fh:
        ry.dump(doc, fh)
    print(f'\nUpdated {_YAML}')


def build_anatomy(owl_path: str, dry_run: bool = False) -> None:
    """
    Rebuild anatomy entries and annotate food material entries with anatomy CURIEs.

    Phase 1: refresh anatomy type entries (UBERON/PO/FAO/FOODON terms under configured
             roots) via the standard build_refresh logic.  During this phase, any
             anatomy term with an 'in taxon' link in the OWL will have its taxon
             ensured in the taxonomy branch and its 'taxonomy' attribute written.
    Phase 2: annotate food material entries with 'anatomy' CURIEs by traversing
             rdfs:subClassOf upward until the first named UBERON/PO/FAO class is found.
    Phase 3: for FOODON anatomy terms, traverse subClassOf upward to find the nearest
             UBERON/PO/FAO ancestor and write it as 'anatomy_ref' on the anatomy entry.
    """
    build_refresh('anatomy', owl_path, dry_run=dry_run)
    _annotate_food_anatomy(owl_path, dry_run=dry_run)
    _annotate_anatomy_refs(owl_path, dry_run=dry_run)


if __name__ == '__main__':
    main()
