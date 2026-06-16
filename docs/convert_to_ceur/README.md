# CEUR-WS PDF Converter

Converts a Google Doc to a CEUR-WS–formatted PDF by downloading the document
directly from its public URL, extracting front-matter content automatically,
and compiling to PDF using either a full LaTeX pipeline (`ceurart.cls`) or
LibreOffice as a fallback.

## CEUR-WS Resources

- **Submission instructions:** <https://ceur-ws.org/HOWTOSUBMIT.html>
- **CEURART styling package** (LaTeX + LibreOffice templates): <http://ceur-ws.org/Vol-XXX/CEURART.zip>  
  The `ceurart.cls` and `elsarticle-num-names.bst` files in this folder were extracted from that zip.
- **ceur-precheck** (validate your PDF before submission): <https://github.com/johnbeve/ceur-precheck>
- **GenAI policy** (required declaration for all submissions): <https://ceur-ws.org/GenAI/Policy.html>

## Files

| File | Purpose |
|------|---------|
| `convert_to_ceur.py` | Main conversion script |
| `paper_config.json` | Persisted paper metadata (authors, affiliations, conference, etc.) |
| `ceurart.cls` | CEUR-WS LaTeX document class (used by tectonic/pdflatex) |
| `elsarticle-num-names.bst` | BibTeX bibliography style (used when `bib_file` is set) |

## Quick Start

```bash
# Best output — install tectonic once (self-contained LaTeX, no MacTeX needed)
brew install tectonic

# First time: provide the Google Doc URL to set up paper_config.json
python3 convert_to_ceur.py -i "https://docs.google.com/document/d/<DOC_ID>"

# Subsequent runs: re-download and regenerate PDF with saved settings
python3 convert_to_ceur.py

# Or force the LibreOffice fallback (already installed on Mac)
python3 convert_to_ceur.py --engine lo
```

Everything goes in the `temp/` subfolder by default (`paper_config.json`, output PDF,
optional `paper.tex`). Use `-f <name>` to use a different folder.

## Google Doc Layout

For the converter to auto-extract metadata, the document must open with a
specific front-matter block **before the first section heading**. The simplest
way to set this up once, then leave it — subsequent runs re-extract
automatically.

### Minimal front-matter template

How it looks in Google Docs (and how it renders as markdown):

> The Full Paper Title Goes Here
>
> Authors: Jane Smith<sup>1,\*</sup>, John Doe<sup>2</sup>, Alice Brown<sup>1,3</sup>
>
> <sup>1</sup> First University, City, Country  
> <sup>2</sup> Second Institution, City, Country  
> <sup>3</sup> Third Institution, City, Country
>
> \* Corresponding author jane.smith@example.com
>
> **ORCIDS:**  
> JS: https://orcid.org/0000-0000-0000-0001  
> JD: https://orcid.org/0000-0000-0000-0002  
> AB: https://orcid.org/0000-0000-0000-0003
>
> **Keywords:** food systems, ontology, processing
>
> *— `# Abstract` heading (Heading 1 style in Google Docs) —*
>
> Your abstract text goes here.
>
> *— `# Introduction` heading (Heading 1 style in Google Docs) —*
>
> Body text begins here …

The equivalent raw markdown syntax (what the converter sees after export):

```markdown
The Full Paper Title Goes Here

Authors: Jane Smith^1,*^, John Doe^2^, Alice Brown^1,3^

^1^ First University, City, Country
^2^ Second Institution, City, Country
^3^ Third Institution, City, Country

* Corresponding author jane.smith@example.com

**ORCIDS:**
JS: https://orcid.org/0000-0000-0000-0001
JD: https://orcid.org/0000-0000-0000-0002
AB: https://orcid.org/0000-0000-0000-0003

**Keywords:** food systems, ontology, processing

# Abstract

Your abstract text goes here.

# Introduction

Body text begins here ...
```

### Field-by-field rules

**Title** — the first non-blank paragraph that does not start with "Authors:".
Plain text or Google Docs "Title" style both work.

**Authors line** — must start with `Authors:` (case-insensitive). List authors
separated by commas. Each name is followed immediately by a superscript
affiliation number (use Google Docs Insert → Special characters → Superscript,
or type `^n^` notation). Mark the corresponding author with an additional `*`
in the superscript, e.g. `^1,*^`. Authors with multiple affiliations use
comma-separated numbers: `^1,3^`.

**Affiliations** — one per paragraph, in the form `^N^ Institution, City, Country`
where `N` matches the number used in the Authors line. Unicode superscript
digits (¹ ² ³) are also recognised.

**Corresponding author email** — a paragraph containing the words
"Corresponding author" followed by the email address (plain text or as a
mailto: link).

**ORCIDs** — under a `**ORCIDS:**` bold heading (or any heading named
"ORCIDs"), one line per author in the form `XX: https://orcid.org/XXXX-...`
where `XX` is the author's initials. The converter matches initials to the
author list; if two authors share initials, add an extra letter (e.g. `JAS`
vs `JAD`).

**Keywords** — a bold inline label: `**Keywords:** word, phrase, word`
(semicolons also work as separators). Place this anywhere in the front-matter
block, typically after affiliations.

**Abstract** — a section headed `# Abstract` (any heading level, or a
standalone `**Abstract**` bold paragraph). The converter strips this section
from the body and places it in the formatted front matter automatically.

**Section headings** — use Google Docs "Heading 1" / "Heading 2" / "Heading 3"
paragraph styles for `\section` / `\subsection` / `\subsubsection` in the
output. Do not type heading numbers manually — `ceurart.cls` numbers sections
automatically. Heading 4 is also supported (renders as `\paragraph`).

### Tips

- Everything before the first `# Heading` is treated as front-matter; the
  converter ignores it in the body and extracts fields from it instead.
- Run `python3 convert_to_ceur.py --extract` after restructuring the
  front-matter to force a re-parse and update `paper_config.json`.
- After the initial extraction you can hand-edit `paper_config.json` to
  correct anything (e.g. a misspelled affiliation) without touching the doc.
- The document sharing must be set to **"Anyone with the link can view"** in
  Google Docs — the converter accesses the export URL directly without signing in.
- **Markdown as input:** the converter currently only accepts Google Docs URLs.
  If your paper is already in a markdown file that follows the same front-matter
  conventions above, the conversion pipeline from the markdown step onward would
  work unchanged — local file input is a planned addition.

## Re-running After Doc Changes

Just re-run the script — it always re-downloads the Google Doc from the live URL:

```bash
python3 convert_to_ceur.py
```

With no `-f` flag the script looks for `paper_config.json` in the `temp/`
subfolder (relative to the script). Use `-f <name>` to point at a different
folder, e.g. `python3 convert_to_ceur.py -f mypaper`.

The abstract is re-extracted from the document on every run. Everything else
(authors, affiliations, keywords, conference) comes from `paper_config.json`
and stays stable between runs.

**Note:** Google's export endpoint can take up to ~5 minutes to reflect a
recent edit. If your changes aren't showing up in the output, wait a few
minutes and re-run.

To refresh all metadata from the current document (e.g., after adding authors or
changing the title), use the `--extract` flag:

```bash
python3 convert_to_ceur.py --extract
```

This re-parses the Google Doc front-matter and overwrites any auto-populated fields
(title, authors, affiliations, ORCIDs, keywords) in `paper_config.json`.
Fields you have hand-edited (conference, copyright, GenAI declaration, etc.) are
not touched unless they were originally extracted from the doc.

## Output Quality

| Engine | How to get it | Output |
|--------|--------------|--------|
| `tectonic` | `brew install tectonic` | Full CEURART layout: author blocks with ORCIDs, conference footer, copyright line, CC-BY icon |
| LibreOffice | Pre-installed on Mac | Converts the Google Doc's own formatting to PDF — no CEURART layout applied |

Install tectonic for submission-ready output.

## What Is Auto-Populated

When the script runs it prints a full summary of the metadata being used.
Things extracted automatically from the Google Doc on each run:

- **Abstract** — pulled from the `# Abstract` section heading in the document body

Things stored in `paper_config.json` (set once, reused on every run):

- **Title** — set manually; update if the doc title changes
- **Authors** — names, affiliation numbers, ORCIDs, email, corresponding-author flag
- **Affiliations** — numbered institution names matching the superscripts in the doc
- **Keywords** — keyword list
- **Conference** — workshop name, date, location
- **Copyright year / clause** — defaults to CC-BY 4.0
- **Acknowledgments**, **GenAI declaration**, **bibliography file**

## paper_config.json Reference

```jsonc
{
  "doc_url": "",                     // set via -i <url> or edit directly
  "export_format": "docx",          // docx recommended; odt also works
                                     // (other document types planned for future support)
  "output_pdf": "my_paper.pdf",

  "pdf_engine": "auto",             // auto | tex | lo

  "title": "Paper Title Here",
  "title_note": "",                 // optional footnote on the title (★ mark)
  "conference": "Workshop Name, Month DD-DD, YYYY, City, Country",
  "copyrightyear": "2026",
  "copyrightclause": "Copyright for this paper by its authors. ...",

  "abstract": "",                   // leave empty to extract from doc automatically
  "abstract_from_doc": true,        // set false to use the abstract field above instead
  "abstract_heading": "Abstract",   // heading text that marks the abstract section

  "keywords": ["keyword1", "keyword2"],
  "keywords_heading": "Keywords",   // heading text to strip from body

  "authors": [
    {
      "name": "Full Name",
      "affils": "1",                // comma-separated affiliation numbers, e.g. "1,2"
      "orcid": "0000-0000-0000-0000",
      "email": "name@example.com",
      "url": "",
      "corresponding": true,
      "equal_contribution": false
    }
  ],

  "affiliations": [
    {"num": "1", "text": "Institution, Address, City, Country"}
  ],

  "cortext": "Corresponding author.",
  "acknowledgments": "",

  // GenAI declaration — see https://ceur-ws.org/GenAI/Policy.html
  // If all three fields are empty the script prompts interactively on first run.
  "genai_uses": [],                 // subset of: text_creation, translation, grammar,
                                    //   rephrasing, image_creation
  "genai_tools": "",                // free text, e.g. "ChatGPT, Grammarly"
  "genai_declaration": "",          // auto-composed from uses+tools; override here if needed

  "bib_file": "",                   // basename of a .bib file (no extension) if using BibTeX

  "pandoc_extra_args": []           // extra flags passed to pandoc, e.g. ["--csl=apa.csl"]
}
```

## Supported Input Formats

Currently the script accepts **Google Docs URLs** (exported as DOCX via the
Google Docs API). Support for additional document types (e.g. local DOCX/ODT
files, Overleaf exports, Markdown) is planned for a future version.

## GenAI Declaration

CEUR-WS requires a Generative AI use statement in every paper:

- **Policy**: <https://ceur-ws.org/GenAI/Policy.html>
- **Full use-case taxonomy**: <https://ceur-ws.org/GenAI/Taxonomy.html>

The script handles this automatically:

- **First run** (when `genai_declaration` is empty): the script prompts you
  interactively to select any applicable use cases and name the tool(s) used,
  then saves the composed text back to `paper_config.json`.
- **Subsequent runs**: the saved declaration is used as-is.
- **No AI used**: just press Enter at the prompt — the standard "no tools used"
  statement is written automatically.
- **Manual override**: set `genai_declaration` to any text you like and the
  prompt is skipped entirely.

Valid use-case keys for `genai_uses` (drawn from the CEUR-WS taxonomy):

| Key | Label |
|-----|-------|
| `text_creation` | Drafting Content |
| `image_creation` | Generate Images |
| `translation` | Text Translation |
| `literature_review` | Generate Literature Review |
| `rephrasing` | Paraphrase and Reword |
| `improve_style` | Improve Writing Style |
| `abstract_drafting` | Abstract Drafting |
| `grammar` | Grammar and Spelling Check |
| `plagiarism_detection` | Plagiarism Detection |
| `citation_management` | Citation Management |
| `formatting` | Formatting Assistance |
| `peer_review` | Peer Review Simulation |
| `content_enhancement` | Content Enhancement |

## Command-Line Options

```
python3 convert_to_ceur.py [options]

  -i, --input URL    Google Doc URL — required when paper_config.json does not exist
                     or doc_url is empty; updates doc_url if a different URL is given
  -f, --folder DIR   Working folder for paper_config.json, output PDF, and paper.tex
                     (relative to the script; default: temp)
  --engine auto      Auto-detect: prefer LaTeX (tectonic/pdflatex), fall back to LibreOffice
  --engine tex       Force LaTeX (exits with error if no engine found)
  --engine lo        Force LibreOffice
  -e, --extract      Re-extract metadata (title, authors, affiliations, keywords) from
                     the current Google Doc, overwriting any previously auto-populated
                     values in paper_config.json
  --keep-tex         Save the intermediate paper.tex in the working folder for inspection
  --settings FILE    Override the settings JSON path (default: <folder>/paper_config.json)
```

By default everything goes in `./temp/`:

```
temp/
  paper_config.json   ← paper metadata (created on first -i run)
  my_paper.pdf        ← final output PDF
  paper.tex           ← intermediate LaTeX (only with --keep-tex)
```

Use `-f` to manage multiple papers from the same script directory:

```bash
# Set up two separate papers
python3 convert_to_ceur.py -f paper_a -i "https://docs.google.com/document/d/<ID_A>"
python3 convert_to_ceur.py -f paper_b -i "https://docs.google.com/document/d/<ID_B>"

# Regenerate each independently
python3 convert_to_ceur.py -f paper_a
python3 convert_to_ceur.py -f paper_b
```

## Current Paper

This folder is configured for:

**The Food Composition Triumvirate: Ingredients, their Characteristics and Processes**  
*Integrated Food Ontology Workshop at JOWO 2026 Episode XII: The Tropical Spring of Ontology,
Sept 21–22, 2026, Vitoria, Brazil*

Authors: Damion Dooley (SFU), Robert Warren, Sarah Brinkley (PTFI/AHA), Hande McGinty (KSU),
Emily Steliotes (IC-FOODS), Anoosha Sehar (SFU), Rhiannon Cameron (SFU),
Colin Fontaine, Magalie Weber (INRAE)
