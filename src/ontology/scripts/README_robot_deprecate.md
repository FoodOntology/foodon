# robot_deprecate.py — FoodOn Term Deprecation Script

Automates the OBO-standard deprecation workflow for FOODON terms that carry
`obo:IAO_0100001` (replaced by) but are not yet marked `owl:deprecated`.
Terms may be defined in the main ontology file **or in any imported component
file** (e.g. `food_products.owl`, `food_materials.owl`, `food_process.owl`).

---

## Minimum annotation required to trigger deprecation

Add **both** of the following annotations to the term in its source file
(usually via Protégé) before running the script:

| Annotation property | IRI | Value | Purpose |
|---|---|---|---|
| `replaced by` | `obo:IAO_0100001` | IRI of the replacement term | Triggers the script; designates the successor |
| `label` | `rdfs:label` | Existing English label | Used to build the `"obsolete: …"` label |

The script acts only on terms that:
- have `obo:IAO_0100001` pointing to a replacement
- are **not** yet marked `owl:deprecated true`
- whose replacement is **not** itself deprecated
- whose IRI matches one of the `--prefix` patterns (default: `FOODON_`)

No other annotation is required to trigger the script, though the richer the
annotations on the deprecated term (definition, synonyms, xrefs), the more
useful the transfer step is.

---

## Command-line usage

```
python3 robot_deprecate.py [-i FILE] [-p PREFIX ...] [--update] [--catalog FILE]

  -i / --input FILE        Main ontology file to search (default: ../foodon-edit.ofn)
  -p / --prefix PREFIX     One or more IRI prefixes for candidate terms
                           (default: FOODON_).  Bare prefixes are expanded to
                           http://purl.obolibrary.org/obo/PREFIX.
  --update                 Apply changes to the source files (default: preview only)
  --catalog FILE           OWL catalog XML (auto-detected next to --input if omitted)
  --deprecation-file FILE  OFN file to receive deprecated records
                           (auto-detected from catalog if omitted)
```

Default (no `--update`): print a **preview report** of every change that
would be made, sectioned by source ontology.  No files are modified.

With `--update`: apply changes directly to the original files.

---

## What the script does — step by step

### 1. Build a merged snapshot

```
robot merge --input <input> --output cache-foodon-merged.owl
```

The merged file is written to `scripts/cache-foodon-merged.owl` and used as
the single source for all subsequent SPARQL SELECTs.  It includes all imports
so already-deprecated terms are automatically excluded by the
`NOT EXISTS { ?x owl:deprecated true }` filter.

### 2. Discover terms queued for deprecation

A preliminary SELECT finds every term matching `--prefix` that has
`obo:IAO_0100001` but not yet `owl:deprecated true`, and that has a
non-deprecated replacement.  If none are found, the script exits immediately.

### 3. Collect annotations and blank-node references (for preview)

Additional SELECTs collect:
- All non-blank-node annotations of each deprecated term (to show what will be
  transferred vs. removed)
- Blank-node references: axioms on **other** terms whose restriction filler is
  the deprecated IRI (e.g. `SubClassOf(?other ObjectSomeValuesFrom(?prop ?x))`)

### 4. Locate source file for each term and replacement

The script searches the main input file plus every file listed in its own
`Import(…)` directives (resolved via the catalog) for a
`Declaration(Class(…))` matching each term IRI.  Only files in the
`components/` folder (or the input file's own directory) are included;
files under `imports/` are excluded because those contain 3rd-party ontologies
that the project does not modify.  The file that declares the class is the
one that will be modified.

### 5. Preview report

The report is sectioned by source ontology and shows for each term:
- The term CURIE and label
- The replacement CURIE, label, and file
- Annotations that will be **copied to the replacement**
- Annotations that will be **removed from the source file**
- Blank-node references that will be **repointed to the replacement**
- The three entries that will be **added to `deprecation_import.ofn`**

### 6. (with `--update`) Auto-add `Declaration(Class(?x))` if missing

Before any SPARQL updates run, missing `Declaration(Class(…))` entries are
inserted into `components/deprecation_import.ofn` immediately after the last
existing `Declaration(Class(…))` line.

### 7. (with `--update`) Insert deprecation record into `deprecation_import.ofn`

Three VALUES-based INSERT queries add to each deprecated term `?x`:

| Annotation | Value |
|---|---|
| `rdfs:label` | `"obsolete: {original label}"@en` |
| `obo:IAO_0100001` (replaced by) | `?replacement` IRI |
| `owl:deprecated` | `"true"^^xsd:boolean` |

### 8. (with `--update`) Delete axioms from each term's source file

A VALUES-based DELETE removes all non-blank-node subject–predicate–object
triples where `?x` is the subject.  If terms are spread across multiple
component files, separate ROBOT calls are made per file.

Blank-node objects (e.g. `SubClassOf(?x ObjectSomeValuesFrom(…))`) are
**not** deleted because they cannot be placed in VALUES clauses; any such
axioms on the deprecated term itself must be removed manually in Protégé.

### 9. (with `--update`) Transfer annotations to replacement

A VALUES-based INSERT copies the following annotations from `?x` to
`?replacement`, targeting the file where the replacement is declared:

- `oboInOwl:hasSynonym`
- `oboInOwl:hasExactSynonym`
- `oboInOwl:hasRelatedSynonym`
- `oboInOwl:hasNarrowSynonym`
- `oboInOwl:hasBroadSynonym`
- `obo:IAO_0000118` (alternative label)
- `oboInOwl:hasDbXref`
- `obo:IAO_0000115` (textual definition)

Curation status (`obo:IAO_0000114`) is not transferred; set it independently.

### 10. (with `--update`) Repoint blank-node restriction fillers

For each file that contains a blank-node restriction referencing a deprecated
term as its filler, a direct SPARQL 1.1 Update is applied:

```sparql
DELETE { ?bn ?p ?x }
INSERT { ?bn ?p ?replacement }
WHERE {
    VALUES (?x ?replacement) { (<old_IRI> <new_IRI>) … }
    ?bn ?p ?x .
    FILTER (isBlank(?bn))
}
```

Explicit `VALUES` bindings are used (rather than querying `owl:deprecated`) so
the update works correctly regardless of import-load order.

---

## Which files are modified (with `--update`)

| File | What changes |
|---|---|
| `components/deprecation_import.ofn` | Declaration + obsolete label + replaced-by + owl:deprecated added per term |
| Source file(s) where deprecated term is declared | All non-blank-node axioms about `?x` removed |
| Source file(s) where replacement is declared | Synonym / definition / xref annotations added (see note below) |
| File(s) containing blank-node references | Restriction fillers repointed to replacement |

Changes are written **directly to the original files** (no `temp_` copies).
Review with `git diff` before committing.

> **Read-only components:** `components/cdno_import.ofn` and `components/food_materials.owl`
> are treated as read-only.  If the replacement term is declared in either file,
> annotation transfers are redirected to `foodon-edit.ofn` instead.  The preview
> report flags this with `[read-only → …]` and the update step prints a `[redirect]`
> line for each affected annotation.

---

## Which ontology is searched

The script queries `scripts/cache-foodon-merged.owl`, a full merge of the
`--input` file and all its imports.  The search is restricted to terms whose
IRI matches `--prefix`:

```sparql
FILTER (STRSTARTS(STR(?x), "http://purl.obolibrary.org/obo/FOODON_"))
```

Multiple prefixes may be given: `-p FOODON_ CDNO_`.

---

## Does `deprecation_import.ofn` receive the deprecated term?

**Yes.**  `components/deprecation_import.ofn` is the permanent record for all
deprecated FOODON terms.  After `--update`, each newly deprecated term appears
there with:
- `Declaration(Class(…))` (auto-added if absent)
- `rdfs:label "obsolete: …"@en`
- `obo:IAO_0100001 <replacement>`
- `owl:deprecated "true"^^xsd:boolean`

The file is included as a component in the ODK `Makefile` and imported by
`foodon-edit.ofn`, so deprecated terms remain visible in the merged ontology
for backward compatibility.

---

## Typical workflow

```bash
# 1. In Protégé, open the source file and add to the term being deprecated:
#      obo:IAO_0100001  <http://purl.obolibrary.org/obo/FOODON_XXXXXXXX>
#    Save the file.

# 2. Preview what will happen:
cd src/ontology/scripts
python3 robot_deprecate.py -i ../foodon-edit.ofn -p FOODON_

# 3. Apply changes:
python3 robot_deprecate.py -i ../foodon-edit.ofn -p FOODON_ --update

# 4. Review the diffs:
git diff ../components/deprecation_import.ofn
git diff ../foodon-edit.ofn
git diff ../components/food_products.owl   # if term was there

# 5. Validate:
cd ..
sh run.sh make test IMP=false MIR=false COMP=false

# 6. Commit if clean.
```

> **Prerequisites:** `robot` on PATH; `config/context.json` present one level above `scripts/`.

---

## Known limitations

- **Blank-node objects of the deprecated term** (e.g. `SubClassOf(?x in taxon some NCBITaxon_X)`)
  are not removed by the DELETE step because blank nodes cannot appear in
  VALUES clauses.  Remove them manually in Protégé before running `--update`.
- **Curation status** (`obo:IAO_0000114`) is not transferred; add it manually
  on the replacement if needed.
- The merged cache (`cache-foodon-merged.owl`) is rebuilt on every run.
  Keep it out of version control (it is in `.gitignore`).
