# Provenance and third-party notices

## Clean-room statement

This code was written without reference to the source of the AGPL-3.0 / GPL-3.0 packages `usdm4-excel`, `usdm3-excel`, `usdm4` or `usdm` (data4knowledge). Only their licence files were read. Nothing from those packages is copied or imported.

The Excel template layout in this repo is an original design. Confirm with your legal / OSS compliance team before commercial use.

## Third-party material used

| Material | Licence | Used for |
|---|---|---|
| CDISC DDF-RA `Deliverables/UML/dataStructure.yml` (© 2022 CDISC) | MIT | Classes A–M of `data/usdm_v4_model.json` (via `tools/build_model_spec.py`) |
| CDISC COSMoS BC export (optional input; sample rows in `tests/fixtures`) | MIT (code) / CC-BY-4.0 (content) | BC library loader. Attribution: "Content based on COSMoS (GitHub) used under the CC-BY-4.0 license." |
| NCI Thesaurus / EVS REST API | NCIt is openly licensed by NCI | Code verification, `data/evs_cache.json` |
| CDISC Controlled Terminology (DDF CT) | CDISC terms of use | C-codes in `ct.py` |
| pdfplumber, openpyxl | MIT | Runtime dependencies |

Two parts of the repo come from our own material rather than third-party sources:

- **USDM classes N–Z** in the model spec were written by hand from the USDM v4 specification. They were checked against Certara's own CTX101-001 example.
- **`data/bc_library_seed.json`** was harvested from Certara's CTX101-001 USDM example, with one code corrected (see `data/seed_issues.json`). Using CDISC Library BC content in production is governed by CDISC's terms.
