# foodon_table.py

A command-line report generator for the FoodOn ontology. It traverses the FoodOn hierarchy and outputs a tab-delimited table of organism, food material, and food product terms, optionally with taxonomy, characteristics, cross-references, synonyms, and definitions.

## Dependencies

- Python 3.10+
- [`owlready2`](https://owlready2.readthedocs.io/) — OWL ontology loading and querying
- [`pygtrie`](https://github.com/google/pygtrie) — prefix trie for dbxref namespace matching
- [`pandas`](https://pandas.pydata.org/) — imported but used internally
- [`robot`](https://robot.obolibrary.org/) — command-line tool required only for the `--fresh` flag

Install Python dependencies:

```bash
pip install owlready2 pygtrie pandas
```

## Setup

The script reads from a pre-merged, reasoned OWL file called `cache-foodon-merged.owl`, which must be present in the same directory as the script (`src/ontology/scripts/`).

To generate or refresh this file from the current `foodon-edit.ofn`:

```bash
python3 foodon_table.py --fresh
```

This runs `robot merge ... reason ...` and writes `cache-foodon-merged.owl`. Subsequent runs can omit `--fresh` and use the cached file.

## Usage

```
python3 foodon_table.py [OPTIONS]
```

## Options

| Flag | Long form | Default | Description |
|------|-----------|---------|-------------|
| `-r` | `--root` | Animal, plant, algae, fungus, process roots | Comma-separated list of ontology term IRIs (e.g. `obo:FOODON_00003004`) to use as roots of the hierarchy traversal. |
| `-d` | `--depth` | *(no limit)* | Maximum depth from each root term to include. Omit to include all depths. |
| `-e` | `--exclude` | *(none)* | Semicolon-separated list of term labels or characteristic labels to exclude. Multiple values must be quoted on the command line (e.g. `"alive;raw;dead"`). Any term whose label matches, or which has a matching characteristic, is filtered out along with its entire subtree. |
| `-m` | `--material` | off | Include `[x] material` parent rows in the output and expand material children. |
| `-p` | `--product` | off | Include `[x] food product` rows as children of corresponding organism terms. |
| `-x` | `--dbxrefs` | *(none)* | Comma-separated list of cross-reference prefixes to include (e.g. `asfis,langual,wd,wikipedia`). Use `*` for all. |
| `-D` | `--definition` | off | Include a definition column populated from the IAO:0000115 annotation. |
| `-s` | `--synonyms` | off | Include a column listing `hasSynonym` and `hasExactSynonym` values. |
| `-M` | `--markdown` | off | Output a markdown table (with header and separator row) instead of tab-delimited text. Label indentation uses `&nbsp;` entities scaled by depth. |
| `-f` | `--fresh` | off | Regenerate `cache-foodon-merged.owl` before running the report. |
|      | `--version` |  | Print version and exit. |

### Default root terms

When `-r` is not specified, traversal starts from:

| Label | ID |
|-------|----|
| animal | `FOODON_00003004` |
| plant by taxonomy | `FOODON_03413357` |
| algae | `FOODON_03411301` |
| fungus | `FOODON_03411261` |
| lichen | `FOODON_03412345` |
| completely executed planned process | `COB_0000035` |

## Output

Tab-delimited rows printed to stdout. Columns that have no content across all rows are omitted from the output automatically. The full possible header row is:

```
id	flags	depth	label	taxonomy	characteristics	dbxrefs	synonyms	definition
```

| Column | Enabled by | Description |
|--------|------------|-------------|
| `id` | always | Term IRI in `obo:FOODON_XXXXXXXX` form |
| `flags` | always | `m` if a `[term] material` class exists; `p` if a `[term] food product` class exists |
| `depth` | always | Depth from root (0 = root term itself) |
| `label` | always | English label, indented proportionally to depth |
| `taxonomy` | data-driven | Semicolon-separated `in taxon` (RO_0002162) values; hidden when no terms have taxonomy |
| `characteristics` | data-driven | Semicolon-separated characteristic labels (RO_0000086 / RO_0000053); hidden when none present |
| `dbxrefs` | `--dbxrefs` | Semicolon-separated cross-references matching the prefix filter |
| `synonyms` | `--synonyms` | Semicolon-separated `hasSynonym` and `hasExactSynonym` values |
| `definition` | `--definition` | IAO:0000115 annotation text for the term |

After the main table, two diagnostic sections may be printed to stdout:

- **Missing material links** — organism terms whose `[x] material` class is not a direct parent.
- **Missing product links** — `[x] food product` terms not linked under the corresponding `[x] material`.

Redirect stdout to capture the table; diagnostic lines appear only when `-m` or `-p` are active.

## Examples

**Basic traversal, no depth limit:**
```bash
python3 foodon_table.py
```

**Limit to depth 4, exclude lifecycle-related terms, include Langual cross-references:**
```bash
python3 foodon_table.py -d 4 -e "alive;raw;dead" -x langual
```

**Include material and product rows, with synonyms:**
```bash
python3 foodon_table.py -m -p -s
```

**Animals only, all cross-references, depth 3:**
```bash
python3 foodon_table.py -r obo:FOODON_00003004 -d 3 -x "*"
```

**Regenerate cache, then run full report to file:**
```bash
python3 foodon_table.py -f -m -p -s -x "langual,wd" > foodon_report.tsv
```

**Full annotated report with definitions, synonyms, and Wikidata cross-references:**
```bash
python3 foodon_table.py -D -s -x wd > foodon_report.tsv
```

**Markdown table output:**
```bash
python3 foodon_table.py -M -d 3 > foodon_report.md
```

## Notes

- The `--exclude` delimiter is **semicolon** (`;`), not comma, because some characteristic labels contain commas. Multiple values must be quoted: `-e "alive;raw;dead"`.
- Traversal is breadth-first within each depth level; children are sorted alphabetically.
- A term encountered via multiple paths is only output once (deduplication via a `processed` set).
- Large ontology loads may require increasing Java heap memory if `robot` is involved: set `ROBOT_JAVA_ARGS=-Xmx6G`.
