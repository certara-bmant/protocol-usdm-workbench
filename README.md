# protocol_to_usdm (POC)

A clean-room toolkit: **protocol PDF → our Excel template → CDISC USDM v4 JSON**. It also runs the other way, **USDM JSON → Excel**, so an existing file can be edited and round-tripped.

It was written from scratch, with no code from the AGPL/GPL `usdm*` packages, against:

- the CDISC DDF-RA USDM v4 model (MIT),
- CDISC/NCI controlled terminology,
- Certara's own CTX101-001 example.

```
protocol.pdf ──extract──▶ workbook.xlsx ──build──▶ usdm_v4.json ──validate──▶ report
                              ▲    (human review / workbench)            │
                              └────────────export◀───────────────────────┘
```

See **SUMMARY.md** for a one-page overview of the POC and **GITHUB.md** for publishing the repository.

## Quick start

Requires Python ≥ 3.10.

```
pip install pdfplumber openpyxl
python -m protocol_to_usdm extract  protocol.pdf  study.xlsx      # PDF -> template (+ extraction report)
python -m protocol_to_usdm build    study.xlsx    study.json      # template -> USDM v4 JSON (+ validation)
python -m protocol_to_usdm validate study.json --all
python -m protocol_to_usdm export   existing.json study.xlsx      # USDM JSON -> editable template
python -m protocol_to_usdm template blank.xlsx
```

Options:

- `--cosmos cdisc_biomedical_concepts_latest.csv` loads the full CDISC BC library. Download it from github.com/cdisc-org/COSMoS, `export/`; it is MIT / CC-BY-4.0.
- `--evs` turns on live NCI EVS lookups for terms that are not in the library.

## Web UI

```
python -m protocol_to_usdm serve            # or double-click run_ui.bat (Windows) / ./run_ui.sh
```

`run_ui.bat` uses the **newest** Python 3.10+ it can find. It tries the `py` launcher (3.16 down to 3.10) first, then the standard python.org install folders, and only uses `python` on PATH (e.g. Anaconda) as a last resort. If the newest Python can't install the dependencies, for example a very new release that has no package wheels yet, it automatically falls back to the next newest.

It creates a private environment in `%LOCALAPPDATA%\protocol-usdm-workbench\venv`, outside OneDrive, and reuses it on later runs. You can control it with:

- `run_ui.bat --reset` rebuilds the environment, e.g. after installing a newer Python.
- `set PYTHON_EXE=C:\path\python.exe` before running forces a specific interpreter.

This opens `http://127.0.0.1:8765/` in your browser. It runs locally, uses only the Python standard library, and nothing leaves the machine unless you pass `--evs`. The same `--cosmos` and `--evs` options apply.

| View | What you can do |
|---|---|
| **Open…** | Load a protocol PDF (runs the extractor), a template workbook (.xlsx) or an existing USDM v4 JSON file, or load the 219659 sample. |
| **Overview** | Key study facts, counts, and the extraction report with REVIEW items highlighted. |
| **Schedule of activities** | One matrix per timeline. Click a cell to toggle X, shift-click to edit footnote markers. Day and window cells are editable. Click an activity to see its BC mapping. Footnotes are listed below the matrix. |
| **Biomedical concepts** | For each activity: mapped BCs and surrogates with method, confidence and evidence. Accept or remove mappings. Search the BC library or EVS and add a concept, or create a sponsor surrogate. |
| **Review queue** | Everything not yet accepted (BCs, surrogates, extraction REVIEW items), with one-click accept or jump to the activity. |
| **Data sheets** | Edit any template sheet in a grid, with CT suggestions on coded columns. |
| **Build & validate** | Build the USDM JSON, see stats, build warnings and validation issues, preview and download the JSON. **Download workbook** saves the reviewed state as .xlsx. |

## Modules

| Module | Role |
|---|---|
| `template.py` | Excel template definition. One row per entity, referenced by name. SoA matrix sheets `SoA_n`. Provenance columns. |
| `build.py` | Workbook → USDM JSON. Ids, Code/AliasCode objects, prev/next chains, timelines/timings/windows, footnote conditions, BC properties. |
| `model.py` | Spec-driven object factory. Every object gets exactly the attributes in `data/usdm_v4_model.json`, so typos fail fast. |
| `ct.py` | Decode/synonym → C-code table. It can be extended per study from the workbook's `Codes` sheet. All codes were checked against NCI EVS on 2026-11-02. |
| `bc_library.py` | BC library plus the tiered matcher (see below). Loaders for a seed JSON, the COSMoS CSV and NCI EVS. |
| `pdf_extract.py` | Layout-aware PDF extraction with pdfplumber (see below). Tries the classic synopsis layout first. |
| `soa_grid.py` | Template-agnostic Schedule of Activities parser. It works from the drawn table grid and handles merged header and data cells, rotated header text, superscript footnote markers, multi-page SoAs, shaded "continuous" bars, CCI redactions, group rows, and frequency tokens such as `X{BID}`. |
| `generic.py` | Fall-backs for TransCelerate CPT / ICH M11-style protocols: numbered sections, the synopsis "Overall Design" table, the two-column Objectives/Endpoints table, 5.1/5.2 criteria, interventions and dose arms, and the lab-test appendix (by term-spotting). It only fills what the primary extractor left empty, and logs every value it sets. |
| `validate.py` | Structural and referential pre-check: required attributes, types, dangling refs, chains, timeline integrity, cells. **CDISC CORE is still the authoritative conformance check.** |
| `export.py` | USDM JSON → workbook (round-trip and editing). |
| `webapp.py`, `web/index.html` | Local web UI: standard-library HTTP server plus a single-file HTML/JS front end. |

`pdf_extract.py` covers:

- synopsis fields,
- identifiers, objectives, endpoints, eligibility, abbreviations and narrative,
- SoA tables, using ruling lines, X marks, footnote markers and shaded "continuous" bars,
- the lab appendix, used for panel expansion.

## How BCs are inferred

Each SoA row is first broken into measurable items. The sources, in order:

1. A curated panel expansion, e.g. `12-lead ECG` → 7 ECG BCs.
2. The parenthetical in the row label, e.g. `Vital Signs (blood pressure, pulse rate, body temperature)`.
3. The protocol's lab appendix table, e.g. `Clinical Laboratory Tests (hematology, …)` → 47 analytes, with urine specimen context for the urinalysis items.
4. Footnote term-spotting. Example: footnote *i* adds respiratory rate at dosing, and footnote *f* adds the hepatitis B core antibody. Anything found this way is flagged for review.

Each item is then matched in tiers:

| Tier | Method | Result |
|---|---|---|
| 1 | Exact or curated synonym (`data/synonyms.json`), specimen-aware | Auto-accepted |
| 2 | Fuzzy match over library names and synonyms | A pluggable `reranker` (e.g. an LLM) can reorder candidates. Scores below 0.9 need review. |
| 3 | NCI EVS concept with no CDISC BC (`data/evs_cache.json` or live `--evs`) | BC with an NCIt code but no properties. Needs review, or a CDISC BC request. |
| 4 | Nothing usable | `BiomedicalConceptSurrogate`. Needs review. |

Every mapping records its source text, method, confidence and review status. These go into the workbook and, through `extensionAttributes`, into the JSON, so a workbench can show why each mapping was made.

`data/seed_issues.json` lists known library problems. Any mapping that hits one of them is forced to review.

## Results on the inputs here (`examples/`)

**Protocol 219659 / 3772-CL-1001** (`219659_*`):

- 3 SoA timelines, 23 activities, 75 BCs and 11 surrogates.
- 10 inclusion and 18 exclusion criteria, 5 objectives and 21 endpoints, 73 abbreviations.
- The USDM JSON validates with **0 errors**.
- The extraction report lists every item that needs review. The main ones:
  - CCI-redacted SoA rows,
  - observer-blind mapped to Double Blind,
  - Study ID 219659 scoped to GSK, taken from the PDF metadata,
  - vaccine-specific immunogenicity samples, which became surrogates.
- An independent check against the PDF confirmed the SoA matrices, criteria, doses (µg) and identifiers. The errors it found have been fixed.

**MK-1654-005** (Merck, TransCelerate CPT layout; `MK-1654-005_*`):

- One SoA spread over 4 pages, with 23 visit columns (rotated headers) and 36 activities.
- 10 inclusion and 29 exclusion criteria, 12 objectives and 13 endpoints, 5 dose arms (4 doses + placebo), 121 abbreviations.
- The USDM JSON validates with **0 errors**.
- An independent review against the PDF found the SoA X marks correct across all 36 rows × 23 columns. The errors it found have been fixed: "Xc" cells spanning Day 27/28, merged exclusion criteria, footnote text, and arms.

`python tests/regression.py` re-checks both protocols. Run it after any extractor change, so a fix for one template can't silently break the other.

**CTX101-001** round trip (JSON → Excel → JSON): **18/18 content sections identical.**

**Issues found in the CTX101 dummy itself:**

- `AliasCode_546` / `Code_1080` are re-used 5 times, giving duplicate ids.
- `Urobilinogen Presence in Urine` carried the Nitrite code C64810. It is corrected to C64816 in our seed.
- The AE BC's code C179175 is "Solicited Adverse Event" in NCIt. This is still open.
- It uses older objective-level decodes ("Trial Primary Objective"; the current DDF CT term is "Study Primary Objective").

## Known limitations / next steps

- **Template coverage.** Two layouts are handled: the classic synopsis (Astellas 219659) and the TransCelerate CPT (Merck MK-1654-005). Other templates will mostly work through the generic section and SoA-grid logic, but expect gaps:
  - SoA tables without ruling lines aren't parsed.
  - Lab tables laid out as free-form grids are only term-spotted, so some analytes can be missed.
  - Non-investigational agents (e.g. a challenge virus) are flagged rather than modelled.
  - Visit windows given only in footnotes (e.g. "post-dosing visits ±1 day") aren't applied.

  An LLM pass, prompted per sheet and constrained to the template columns, is the natural next step for the remaining gaps. Keep the deterministic SoA grid parser, which LLMs do less reliably.
- **Not modelled yet:** amendments, estimands, sub-section narrative numbering (missing from the PDF text layer), and procedures.
- **Seed BC library.** The seed holds only the 83 BCs from CTX101. Load the COSMoS export (`--cosmos`) for full CDISC coverage, and extend `synonyms.json` with sponsor vocabulary.
- **Screening windows.** "Day −28 to −3" is stored as nominal Day −28 with Window + 25 (same convention as CTX101). Confirm this is the convention your workbench expects.
- **Verify before relying on the output.** Run `tools/verify_ct.py [study.json]` (needs internet) to re-check all C-codes against EVS. Run CDISC CORE for formal conformance.

## Licensing / provenance

See `NOTICE.md`.
