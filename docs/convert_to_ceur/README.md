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

## Re-running After Doc Changes

Just re-run the script — it always re-downloads the Google Doc from the live URL:

```bash
python3 convert_to_ceur.py
```

The abstract is re-extracted from the document on every run. Everything else
(authors, affiliations, keywords, conference) comes from `paper_config.json`
and stays stable between runs.

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

## Google Doc Requirements

- Sharing must be set to **"Anyone with the link can view"**
- The abstract should be under a heading named `Abstract` (any heading level)
- The script strips the Abstract and Keywords sections from the body so they
  appear only in the formatted front matter

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
