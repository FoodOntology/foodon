# foodon_mapper — Ingredient Recogniser

Maps free-text food ingredient strings to [FoodOn](http://purl.obolibrary.org/obo/foodon.owl) ontology terms. Coverage and synonym data live in `ingredients.yaml`; matching logic lives in `ingredients.py`.

---

## Pipeline

The recogniser applies three stages in order; **the first match wins**.

| Stage | Description |
|-------|-------------|
| 1. **Assumption rules** | If the normalised ingredient text is an exact key in the `assumptions` dict (e.g. `egg → chicken egg`, `milk → cow milk`), substitute the value and continue to stage 2. |
| 2. **Global exact match** | Look up the (possibly substituted) text in a single alias + food-form index built from **all** types simultaneously. Returns the matched entry's `type`/`subtype` directly. |
| 3. **Component search** | Applied only when stages 1–2 fail. Sub-steps tried in order: plant prefix stripping (`wild`, `whole`), parenthetical qualifier stripping `base (qualifier)`, global adjective stripping, characteristic stripping loop, trailing-form stripping (e.g. `L. acidophilus bacterial culture`), anatomy standalone match. |

Type and subtype classification (fruit, grain, dairy, etc.) are metadata fields on each YAML entry, **not** sequential match steps. They are set by `--rebuild TYPE` and read from the matched entry.

---

## Type classifications and OWL roots

These are the recognised ingredient categories, their OWL roots used for `--rebuild TYPE`, and a brief description.

| Type | OWL roots | Description |
|------|-----------|-------------|
| `fruit` | PO:0009001, FOODON:00001057 | Fresh, dried, and processed fruit forms |
| `nut` | FOODON:00005735 | Nut food products |
| `legume` | FOODON:00001264, FOODON:03301467 | Pulses, beans, lentils, soy products |
| `grain` | FOODON:00001093 | Cereal grain and pseudocereal food products |
| `seed` | FOODON:00001173 | Plant seed food products (oilseeds, edible seeds) |
| `root_vegetable` | FOODON:00002150, PO:0009005 | Taproots, bulbs, corms, rhizomes, tubers |
| `spice` | FOODON:03303380 | Spice food products |
| `herb` | FOODON:00003042 | Herb food products |
| `dairy` | FOODON:00001256 | Milk, cream, butter, cheese, yogurt, and other dairy |
| `animal` | FOODON:03420164 | Animal material food products (meat, poultry, seafood, eggs) |
| `lipid` | FOODON:00002664 | Cooking oils and animal fats |
| `fermentation` | FOODON:00001258 | Fermented food products (yogurt, kefir, kimchi, vinegar, etc.) |
| `sweetener` | FOODON:00002300 | Sugars, sugar alcohols, syrups, natural sweeteners |
| `chemical` | FOODON:03412972, CHEBI:60004 | Food additives and chemical mixtures |
| `nutrient` | CHEBI:33229, CDNO:0000001 | Vitamins, minerals, macronutrients, fatty acids, bioactives |
| `taxonomy` | NCBITaxon:2 | Organism/taxon annotation terms (bacteria, probiotic strains) |
| `anatomy` | UBERON:0000061, PO:0025131, FAO:0000001, FOODON:03530146 | Anatomical structure annotation terms |
| `characteristic` | COB:0000502 | Food quality and state terms (raw, dried, frozen, …) |

The `nutrient` type has dedicated sub-recognisers for vitamins (with letter-pattern matching and "as X" annotation handling), minerals, macronutrients, fatty acids, and bioactives.

The `characteristic` type strips a matched quality/state term from the ingredient string and retries the global exact match on the residual, enabling composite matches such as `"frozen blueberry"` → characteristic:frozen + food:blueberry.

---

## Matching tiers

Within each type, matching is attempted in this order:

1. **Direct match** — exact alias or food-form lookup in the configured tier order (`alias_first` or `food_form_first`).
2. **Adjective stripping** — strips configured adjectives from the front of the string, reconstructs the parenthetical form (e.g. `"dried mango"` → `"mango (dried)"`), and retries.
3. **Fallback suffix** — appends a configured fallback word (e.g. `leaf` for herbs, `grain` for grains) and retries.
4. **Trailing annotation strip** — removes a trailing `(…)` clause and retries; the clause becomes residual text, yielding a `partial` match. Example: `"bacterial culture (bifidobacterium lactis bb-12)"` → `bacterial food culture` + residual `"bifidobacterium lactis bb-12"`.

Food-form matches support `"ingredient (qualifier)"` label variants automatically via parenthetical permutation (e.g. `"cinnamon (ground)"` → also indexes `"ground cinnamon"`).

---

## Match statuses

| Status | Meaning |
|--------|---------|
| `exact` | The full ingredient string matched a specific ontology term |
| `composite` | The full string is accounted for by a material term + one or more characteristics |
| `partial` | One or more component terms matched; remainder is unresolved |
| `parent` | A broader group term matched; the specific form is likely a narrower child not yet in the ontology |
| `no_match` | Nothing matched |

---

## Ingredient types

### nutrient

Maps dietary nutrients to FoodOn/CHEBI/CDNO terms.

| Sub-type | Coverage |
|----------|----------|
| `vitamin` | Vitamins A, B1–B12, C, D, E, K; canonical letter notation, abbreviations, chemical forms (`thiamine hydrochloride`), and "as X" parenthetical annotations (`vitamin C (as ascorbic acid)`) |
| `mineral` | 16 dietary minerals via CDNO; common salt forms (e.g. `calcium carbonate`) via CHEBI |
| `macronutrient` | Protein, carbohydrate, dietary fibre (soluble/insoluble) |
| `fatty_acid` | Omega-3/omega-6 families; ALA, DHA, EPA, GLA, linoleic acid; MUFA, PUFA, trans fatty acids |
| `bioactive` | Choline, taurine, inositol, myo-inositol, cholesterol |

### sweetener

Maps sugars and sweeteners to FoodOn/CHEBI/UBERON terms.

| Sub-type | Examples |
|----------|----------|
| `monosaccharide` | glucose (dextrose), fructose, galactose |
| `disaccharide` | sucrose, lactose, maltose, trehalose |
| `sugar_alcohol` | sorbitol, mannitol, xylitol, erythritol, maltitol |
| `polysaccharide` | inulin, raffinose |
| `oligosaccharide` | fructooligosaccharide (FOS), galactooligosaccharide (GOS) |
| `food_product` | cane sugar, brown sugar, corn syrup, HFCS, maple syrup, honey, agave, stevia, coconut sugar |

### fruit

Maps fruit food products to FoodOn terms.

| Sub-type | Examples |
|----------|----------|
| `pome` | apple, pear |
| `berry` | blueberry, cranberry, grape, avocado, pomegranate, kiwifruit, guava |
| `aggregate` | strawberry, raspberry, blackberry |
| `drupe` | peach, plum, cherry, apricot, mango, date, coconut |
| `citrus` | orange, lemon, lime, grapefruit |
| `tropical` | banana, pineapple, papaya, passion fruit |
| `melon` | watermelon, cantaloupe |
| `other` | fig |

Food forms (raisin, prune, dried mango) are indexed as child entries under the parent fruit term. The `whole` and `wild` prefixes are stripped pre-pipeline and re-attached as characteristic components on a match.

> **Note:** Spice fruits (chilli, vanilla, star anise) → `spice`. Root vegetables (beetroot, carrot) → `root_vegetable`.

### root_vegetable

| Sub-type | Examples |
|----------|----------|
| `taproot` | beetroot, carrot, parsnip, turnip, radish, celeriac |
| `bulb` | onion, garlic, leek, shallot, fennel bulb |
| `corm` | taro, konjac |
| `rhizome` | ginger, turmeric, galangal, horseradish, lotus root, wasabi |
| `tuber` | potato, sweet potato, yam, Jerusalem artichoke, cassava |

> **Note:** Ginger, turmeric, and galangal also appear as `spice` rhizome entries; `root_vegetable` covers their fresh/whole food product forms.

### dairy

| Sub-type | Examples |
|----------|----------|
| `milk` | cow, goat, sheep, buffalo, camelid, equine, human; skim/reduced-fat variants |
| `cream` | heavy cream, whipping cream, half-and-half, clotted cream |
| `butter` | butter, ghee, clarified butter, sour cream, crème fraîche |
| `cheese` | cheddar, mozzarella, parmesan, gouda, brie, feta, ricotta, cottage cheese, cream cheese, and more |
| `yogurt` | plain/Greek yogurt, kefir, labneh, skyr, buttermilk |
| `whey` | sweet whey, acid whey |
| `frozen` | ice cream, gelato, sherbet, kulfi |
| `concentrated` | evaporated milk, condensed milk, powdered milk |

### spice

Maps spice food products to FoodOn terms under FOODON:03303380.

| Sub-type | Examples |
|----------|----------|
| `bark` | cinnamon, cassia |
| `berry` | allspice, peppercorn (black/white/pink), Sichuan pepper |
| `bud` | clove, caper |
| `flower` | saffron |
| `fruit` | star anise, hot pepper (cayenne, chilli, paprika), vanilla |
| `leaf` | coriander leaf, curry leaf |
| `resin` | asafoetida |
| `root` | ginger, turmeric, licorice, sarsaparilla |
| `seed` | anise, caraway, cardamom, cumin, mustard seed, nutmeg, fenugreek, and more |
| `blend` | curry powder, Chinese five spice, za'atar, pumpkin pie spice |

### herb

Maps herb food products to FoodOn terms under FOODON:00003042.

| Sub-type | Examples |
|----------|----------|
| `leaf` | rosemary, thyme, basil, oregano, sage, parsley, mint, dill, bay leaf, tarragon, chive, cilantro, lemongrass |

Bare herb names (e.g. `"rosemary"`) are retried as `"rosemary leaf"` via the `leaf` fallback.

### seed

Maps plant seed food products to FoodOn terms under FOODON:00001173.

| Sub-type | Examples |
|----------|----------|
| `oilseed` | sesame (incl. tahini, sesame oil), sunflower, flaxseed/linseed, safflower, canola/rapeseed |
| `edible_seed` | chia, hemp (hearts/hulled), poppy seed, pumpkin seed/pepita, watermelon seed |

> **Note:** Spice seeds (anise, caraway, cardamom, cumin, etc.) → `spice`. Grain sub-branch → `grain`.

### grain

Maps cereal and pseudocereal food products to FoodOn terms under FOODON:00001093.

| Sub-type | Examples |
|----------|----------|
| `cereal_grain` | oat, wheat (incl. durum), rice, barley, corn/maize, rye, millet, sorghum, spelt, farro |
| `pseudocereal` | quinoa, buckwheat |

Food forms indexed per grain: kernels, flour, bran, flakes, rolled forms, groats, semolina, bread, pasta, and speciality forms (popcorn, wild rice, Arborio rice, soba noodles).

### lipid

Maps cooking oils and animal fats to FoodOn terms.

| Sub-type | Examples |
|----------|----------|
| `vegetable_oil` | olive oil, canola/rapeseed oil, soybean oil, corn oil, sunflower oil, rice bran oil |
| `nut_oil` | peanut/groundnut oil, almond oil, walnut oil, hazelnut oil |
| `tropical_oil` | coconut oil, palm oil, palm kernel oil |
| `animal_fat` | lard, tallow, dripping, schmaltz, duck fat, goose fat, suet |
| `marine_oil` | fish oil, cod liver oil, krill oil, algae oil |
| `specialty` | avocado oil, grape seed oil, flaxseed oil, hemp oil, argan oil |
| `blended` | shortening, margarine, cooking spray |

> **Note:** Sesame oil and other seed oils used as food ingredients → `seed`. Butter and ghee → `dairy`. Fish oil / omega-3 supplements → `nutrient`.

### chemical

Maps food additives and chemical mixtures to FoodOn/CHEBI terms.

OWL roots:
- `FOODON:03412972` — food additive: emulsifiers, preservatives, antioxidants, colours, acidity regulators, flavour enhancers, sweeteners, anti-caking agents, humectants, thickeners, and more.
- `CHEBI:60004` — chemical mixture: agar, gelatin, lecithin, bentonite, propolis extract, dimethicone/silicone polymers, etc.

E-numbers (e.g. `e322`, `e471`) are indexed as synonyms and matched directly.

### fermentation

Maps fermented food products and fermentation microorganisms to FoodOn/NCBITaxon terms.

OWL roots:
- `NCBITaxon:2` — Bacteria (starter cultures, probiotic strains)
- `FOODON:00001258` — food (fermented): yogurt, kefir, kimchi, sauerkraut, miso, tempeh, vinegar, etc.

### characteristic

Maps food quality and state terms to ontology terms under COB:0000502 (characteristic).

| Group | Examples |
|-------|----------|
| Viability | alive, dead |
| Thermal | chilled, frozen, flash frozen, thawed |
| Processing | raw, dried, heat-treated, fully heat-treated |
| Physical | sliced, chopped, diced, ground, halved, quartered |
| Ripeness | unripe, ripe, overripe, slightly ripe |
| Doneness | rare, medium-rare, medium, well done |

The full term set is loaded at runtime by BFS traversal of COB:0000502 in `cache-foodon-merged.owl`. Two subtrees are excluded: `BFO:0000017` (realizable entities) and `PATO:0103000` (quantitative qualities). Supplemental informal aliases (e.g. `"fresh"`, `"whole"`) are defined in `_SUPPLEMENTAL` in `ingredients.py` and overlay the OWL-derived lookup.

---

## ingredients.yaml schema

The file has two top-level sections: `configuration` and `ingredient`.

### configuration

Each type key holds:

```yaml
configuration:
  grain:
    description: |        # Human-readable description of coverage
      ...
    roots:                # OWL root CURIEs for --rebuild BFS traversal
    - FOODON:00001093
    subtypes: [...]       # Valid subtype values for entries of this type
    tier_order: food_form_first   # or alias_first
    fallbacks: [grain]    # Words appended as a last-resort retry
    adjectives: [...]     # Strippable prefix adjectives (e.g. "whole", "rolled")
    exclude:              # Optional: CURIEs whose subtrees are skipped during --rebuild
      BFO:0000017: description
```

### ingredient entries

```yaml
ingredient:
  CHEBI:12777:            # CURIE (ontology ID)
    type: nutrient        # Must match a key in configuration
    subtype: vitamin      # String or list[str]; values from configuration.subtypes
    label: vitamin A      # Primary display label (from OWL rdfs:label)
    title: vitamin D3     # Optional alternate title used for letter-pattern matching
    synonyms:             # OWL-sourced synonyms (hasExactSynonym, hasSynonym);
    - retinol             #   replaced on --rebuild
    ai_synonyms:          # AI-suggested or manually curated synonyms
      retinoids:
        status: proposed  # proposed | ok | reject
      vit a:
        status: ok        # included in default matching
        see_also: https://...
    parent:               # Optional: makes this a food-form child of another entry
    - CHEBI:12777
    food_forms:           # Optional: dict of child form CURIEs → list of form labels
      CHEBI:17579:
      - beta-carotene
      - β-carotene
    comment: |            # Optional: appears in the Notes column of reports
      ...
    see_also: https://... # Optional: reference URL
```

#### Synonym fields

| Field | Included in matching | Source | Replaced on `--rebuild` |
|-------|---------------------|--------|------------------------|
| `synonyms` | Always | OWL (`hasExactSynonym`, `hasSynonym`) | Yes |
| `ai_synonyms[status=ok]` | Always | AI-generated / curated | No |
| `ai_synonyms[status=proposed]` | Only with `--synonyms proposed` | AI-generated | No |
| `ai_synonyms[status=reject]` | Never | Explicitly excluded | No |

E-numbers (e.g. `e322`) are kept in `synonyms` rather than migrated to `ai_synonyms`.

---

## CLI usage

```bash
# Map a TSV/CSV file (pipeline mode)
python3 -m foodon_mapper.ingredients -i ingredients.tsv
python3 -m foodon_mapper.ingredients -i ingredients.tsv -o results.tsv
python3 -m foodon_mapper.ingredients -i ingredients.tsv --format html
python3 -m foodon_mapper.ingredients -i ingredients.tsv --format markdown,html

# Test a single type interactively
python3 -m foodon_mapper.ingredients --type grain "rolled oats"
python3 -m foodon_mapper.ingredients --type nutrient "vitamin B12"
python3 -m foodon_mapper.ingredients --type fermentation "bacterial culture"

# Test a type against a TSV file
python3 -m foodon_mapper.ingredients --type fruit --tsv my_file.tsv --column ingredient

# Include proposed ai_synonyms in matching
python3 -m foodon_mapper.ingredients -i ingredients.tsv --synonyms proposed

# Rebuild/refresh ingredients.yaml from OWL (dry run)
python3 -m foodon_mapper.ingredients --rebuild grain --dry-run
python3 -m foodon_mapper.ingredients --rebuild all --dry-run

# Rebuild and write changes
python3 -m foodon_mapper.ingredients --rebuild nutrient
```

### Key flags

| Flag | Description |
|------|-------------|
| `-i / --input` | Input CSV/TSV file or URL |
| `-o / --output` | Output file (default: stdout for TSV, `report.md`/`report.html` otherwise) |
| `--format` | Output format: `tsv` (default), `markdown`, `html` (comma-separated for multiple) |
| `-c / --column` | Ingredient column name in input file (default: `ingredient`) |
| `--type TYPE` | Test a single recogniser type |
| `--synonyms proposed` | Also include `ai_synonyms` with `status: proposed` in lookup indices |
| `--rebuild TYPE\|all` | Refresh `ingredients.yaml` from OWL for one or all types |
| `--dry-run` | With `--rebuild`: report changes without writing |
| `--owl PATH` | Path to merged OWL file (default: `cache-foodon-merged.owl`) |
| `-f / --fresh` | Regenerate `cache-foodon-merged.owl` from `../foodon-edit.ofn` via ROBOT (`merge` → `reason` ELK → `relax --include-subclass-of`) before running. Requires `robot` on `PATH`; run from `scripts/`. |

---

## Build / refresh

### Initial setup — `--rebuild all`

`--rebuild all` is the **initial step** for populating `ingredients.yaml` from scratch. It performs a BFS traversal of the following top-level OWL roots, writes every discovered term into `ingredients.yaml`, then runs per-type classification, anatomy annotation, and taxonomy annotation:

| OWL root | Label |
|----------|-------|
| `FOODON:00001714` | food material by component |
| `FOODON:00001002` | food product |
| `FOODON:03420116` | organism material |
| `FOODON:00002373` | food by meal type |
| `CDNO:0000001` | dietary chemical component (nutrients, vitamins, minerals) |

```bash
# Regenerate cache-foodon-merged.owl from foodon-edit.ofn first, then do a full refresh
python3 ingredients.py --fresh --rebuild all --owl cache-foodon-merged.owl --dry-run

# Initial population / full refresh (reuses the existing cache-foodon-merged.owl)
python3 ingredients.py --rebuild all --owl cache-foodon-merged.owl --dry-run
python3 ingredients.py --rebuild all --owl cache-foodon-merged.owl
```

### Per-type refresh — `--rebuild TYPE`

After initial setup, individual types can be refreshed independently. The command performs a BFS traversal of the type's configured `roots` (see table above) and compares discovered terms against the current `ingredients.yaml` entries:

- **New** terms (in OWL but not in YAML) — added with label and OWL synonyms; `type` is set to TYPE
- **Possibly deprecated** terms (in YAML but not in OWL) — flagged for review; not removed automatically
- **Synonym updates** — the `synonyms` list is replaced with the current OWL synonym set
- **Migrations** — existing `synonyms` not found in OWL are moved to `ai_synonyms[status=proposed]`

`ai_synonyms` are never removed or overwritten by a rebuild.

```bash
# Preview changes without writing
python3 ingredients.py --rebuild grain   --owl cache-foodon-merged.owl --dry-run

# Rebuild a single type
python3 ingredients.py --rebuild grain   --owl cache-foodon-merged.owl
python3 ingredients.py --rebuild dairy   --owl cache-foodon-merged.owl
python3 ingredients.py --rebuild taxonomy --owl cache-foodon-merged.owl
```
