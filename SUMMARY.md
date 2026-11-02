# Protocol → USDM Workbench: POC summary

**Status:** proof of concept · **Owner:** Ben Mant (Certara) · **Date:** November 2026

## 1. Purpose

Show how an unstructured clinical-trial protocol (a PDF) becomes structured, machine-readable **CDISC USDM v4** that a workbench tool can consume. The hard part is the **biomedical concepts (BCs)**: each Schedule of Activities row has to be linked to CDISC BCs / NCI Thesaurus concepts, and the protocol never states those links explicitly.

## 2. What was built

A Python toolkit plus a local web UI. It was written clean-room: no code from the AGPL/GPL `usdm*` packages, so it is free of their licence terms.

```
protocol.pdf ──extract──▶ Excel template ──build──▶ USDM v4 JSON ──validate──▶ report
                    ▲        (review in web UI / Excel)          │
                    └──────────────── export ◀────────────────────┘
```

| Component | What it does |
|---|---|
| **PDF extractor** (`pdf_extract.py`, `soa_grid.py`, `generic.py`) | Reads titles, identifiers, design, population, interventions/doses/arms, objectives and endpoints, eligibility, abbreviations, narrative, Schedule of Activities, footnotes and the lab appendix. Covers two protocol layouts: the classic synopsis and the TransCelerate CPT / ICH M11 style. |
| **SoA grid parser** | Works from the table's drawn grid. It handles merged and rotated header cells, superscript footnote markers, multi-page tables, shaded "continuous" bars, CCI redactions, group rows and in-cell frequencies such as `X{BID}`. |
| **Excel template** (`template.py`) | Our own design: one sheet per entity, referenced by name, plus SoA matrix sheets. Every mapping carries provenance (source text, method, confidence, review status). |
| **Builder** (`build.py`, `model.py`) | Turns the workbook into USDM v4 JSON. It generates ids and Code/AliasCode objects, timelines, timings and windows, footnote conditions, and CDISC BC properties. Objects are created from the USDM model specification, so typos fail immediately. |
| **Validator** (`validate.py`) | Structural and referential pre-check against the USDM v4 model. CDISC CORE remains the formal conformance check. |
| **Exporter** (`export.py`) | Turns USDM JSON back into the workbook, for round-trip testing and editing existing files. |
| **BC library and matcher** (`bc_library.py`) | Tiered matching against CDISC BCs, the COSMoS export, curated synonyms and NCI EVS. Results go into a review queue. |
| **Web UI** (`webapp.py`, `web/index.html`) | A local browser app, started with `run_ui.bat`. It lets you upload a PDF, workbook or JSON, edit the SoA, review and fix BC mappings, edit any sheet, then build, validate and download. It uses only the Python standard library, and no data leaves the machine. |
| **Controlled terminology** (`ct.py`) | Decode/synonym → C-code table. Every code was checked against NCI EVS, and the `Codes` sheet extends it per study. |

## 3. How BCs are inferred

1. **Break each SoA row into measurable items.** Sources:
   - curated panel expansions, e.g. ECG → 7 BCs;
   - the parenthetical in the row label, e.g. `Vital Signs (BP, pulse, temperature)`;
   - the protocol's lab table, e.g. "Safety Laboratory Test" → individual analytes, with urine/serum specimen context;
   - footnote term-spotting, e.g. respiratory rate found only in a footnote.
2. **Match each item, in tiers:**

   | Tier | Method | Typical confidence | Outcome |
   |---|---|---|---|
   | 1 | Exact or curated synonym | 1.0 | Auto-accepted |
   | 2 | Fuzzy match; a pluggable re-ranker (e.g. an LLM) can be added | 0.60–0.95 | Reviewed |
   | 3 | NCI EVS concept with no CDISC BC | 0.65 | Reviewed, or a request for a new BC |
   | 4 | Nothing usable | – | Sponsor-defined surrogate |
3. **Guardrails:**
   - every C-code is checked against EVS (`tools/verify_ct.py`);
   - known library defects (`data/seed_issues.json`) force review;
   - provenance travels into USDM `extensionAttributes`, so a workbench can show *why* each mapping was made.

## 4. Results

| | 219659 / 3772-CL-1001 (Astellas) | MK-1654-005 (Merck) | CTX101-001 (Certara dummy) |
|---|---|---|---|
| Layout | Classic synopsis, 3 SoA tables, CCI-redacted | TransCelerate CPT, 1 SoA over 4 pages, rotated headers | USDM JSON input |
| Timelines / visit columns | 3 / 5, 5, 3 | 1 / 23 | 2 |
| Activities | 23 | 36 | 24 |
| Biomedical concepts / surrogates | 75 / 11 | 66 / 24 | 83 / 6 |
| Criteria (inclusion / exclusion) | 10 / 18 | 10 / 29 | 12 |
| Objectives / endpoints | 5 / 21 | 12 / 13 | 2 / 3 |
| USDM validation | **0 errors** | **0 errors** | Round trip: **18/18 sections identical** |

Both PDF extractions were reviewed independently against the source, and the discrepancies found were fixed. For MK-1654-005 the review checked every SoA cell (36 rows × 23 columns). `tests/regression.py` now locks in both results.

**Issues found in the CTX101 dummy along the way:**

- two ids are each reused 5 times;
- urobilinogen carried the nitrite code (C64810, corrected to C64816 in the seed library);
- the AE BC's code C179175 is "Solicited Adverse Event" in NCIt (still open);
- it uses older objective-level decodes.

## 5. Licensing decision

`usdm4-excel` / `usdm3-excel` are AGPL-3.0, and `usdm4` / `usdm` are GPL-3.0. They are copyleft, which is unsuitable for embedding in a closed or hosted commercial tool without a separate licence. This POC therefore uses only:

- the CDISC DDF-RA model (MIT);
- COSMoS BC content (MIT / CC-BY-4.0);
- NCI EVS;
- Certara's own example data.

See `NOTICE.md`. **Legal / OSS compliance sign-off is still required** before commercial use.

## 6. Limitations

- **Other protocol layouts:** extraction is rule-based. Templates beyond the two tested will mostly work through the generic logic, but expect gaps.
- **Unruled SoA tables:** SoA tables without ruling lines aren't parsed.
- **Free-form lab tables:** these are only term-spotted, so some analytes can be missed (e.g. alkaline phosphatase, troponin in MK-1654-005).
- **Seed BC library:** it holds only 83 CDISC BCs. Load the full COSMoS export with `--cosmos` for real coverage.
- **Not modelled yet:** amendments, estimands, procedures, and non-investigational agents (e.g. a challenge virus, which is flagged).
- **Footnote-only windows:** visit windows given only in footnotes aren't applied.
- **Formal conformance:** the validator is a pre-check; CDISC CORE has not been run.

## 7. Recommended next steps

1. **LLM extraction pass:** prompt it per template sheet, with output constrained to the template columns, for layouts the rules miss. Keep the deterministic SoA grid parser.
2. **Full COSMoS library:** load it, add sponsor synonyms, and add an LLM re-ranker for tier-2 BC matches.
3. **Conformance and wiring:** run CDISC CORE on the outputs, and connect the workbench to the JSON.
4. **More test protocols:** add protocols from other sponsors to `tests/regression.py`.
5. **Productionise the UI:** auth, multi-user storage and hosting, if it becomes a team tool.

## 8. Quick start

```
run_ui.bat                                           # Windows: picks Python 3.10+, opens the web UI
python -m protocol_to_usdm extract protocol.pdf study.xlsx
python -m protocol_to_usdm build   study.xlsx    study.json
python tests/test_basic.py && python tests/regression.py && python tests/roundtrip.py inputs/CTX101-001_USDM_v4.json
```

See `README.md` for full details.
