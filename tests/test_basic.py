"""Basic tests - run with `python -m pytest tests` (or `python tests/test_basic.py`)."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol_to_usdm.bc_library import BCLibrary  # noqa: E402
from protocol_to_usdm.build import Builder, build  # noqa: E402
from protocol_to_usdm.ct import CTError, Terminology  # noqa: E402
from protocol_to_usdm.template import read_workbook, write_workbook  # noqa: E402
from protocol_to_usdm.validate import validate  # noqa: E402

MINI = {
    "Study": {"name": "DEMO-001", "version": "1.0", "sponsor": "Acme", "phase": "phase 1", "model": "parallel",
              "approval_date": "2026-01-15"},
    "Population": {"name": "POP", "healthy": "Y", "min_age": 18, "max_age": 55, "sex": "Female; Male"},
    "Organizations": [{"name": "Acme", "type": "sponsor", "identifier": "123"}],
    "Identifiers": [{"text": "DEMO-001", "organization": "Acme"}],
    "Titles": [{"type": "official", "text": "A demo study"}],
    "Arms": [{"name": "A", "type": "investigational"}],
    "Epochs": [{"name": "SCR", "type": "screening"}, {"name": "TRT", "type": "treatment"}],
    "Elements": [{"name": "E1"}],
    "Cells": [{"arm": "A", "epoch": "SCR", "elements": "E1"}, {"arm": "A", "epoch": "TRT", "elements": "E1"}],
    "Eligibility": [{"identifier": "1", "category": "inclusion", "text": "Adult"}],
    "Encounters": [{"name": "V1"}, {"name": "V2"}],
    "Activities": [{"name": "VS", "biomedical_concepts": "Pulse"}, {"name": "PK", "surrogates": "PK sample"}],
    "BiomedicalConcepts": [{"name": "Pulse", "ncit_code": "C49676"}],
    "BCSurrogates": [{"name": "PK sample"}],
    "Conditions": [{"name": "PRE", "marker": "†", "text": "pre-dose"}],
    "Timelines": [{"name": "MAIN", "main": "Y"}],
    "SoA": [{"sheet": "SoA_1", "timeline": "MAIN", "columns": [
        {"epoch": "SCR", "encounter": "V1", "day": -7, "window_lower": 0, "window_upper": 3},
        {"epoch": "TRT", "encounter": "V2", "day": 1}],
        "rows": [{"activity": "VS", "cells": ["X", "X†"]}, {"activity": "PK", "cells": [None, "X"]}]}],
}


def test_ct_lookup():
    ct = Terminology()
    assert ct.lookup("phase", "Phase 1/2")[0] == "C15693"
    assert ct.lookup("unit", "µg")[0] == "C48152"
    try:
        ct.lookup("phase", "phase 9")
        assert False
    except CTError:
        pass


def test_build_minimal_valid():
    doc, warns = build(MINI)
    errs = [i for i in validate(doc) if i["severity"] == "error"]
    assert not errs, errs
    d = doc["study"]["versions"][0]["studyDesigns"][0]
    tl = d["scheduleTimelines"][0]
    t = {x["valueLabel"]: x for x in tl["timings"]}
    assert t["Day 1"]["type"]["code"] == "C201358"
    assert t["Day -7"]["value"] == "P7D" and t["Day -7"]["type"]["code"] == "C201357"
    assert t["Day -7"]["windowUpper"] == "P3D"
    bc = doc["study"]["versions"][0]["biomedicalConcepts"][0]
    assert bc["properties"], "Pulse should pick up CDISC BC properties from the seed library"
    cond = doc["study"]["versions"][0]["conditions"][0]
    assert cond["contextIds"] and cond["appliesToIds"], "footnote marker should link condition to instance + activity"


def test_clock_timings():
    assert Builder._point("-1h")[0] == -60 and Builder._point("30m")[0] == 30
    assert Builder._iso(0, clock=True) == "PT0H" and Builder._iso(1440) == "P1D"


def test_workbook_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "mini.xlsx"
        write_workbook(MINI, p, Terminology())
        back = read_workbook(p)
        assert back["SoA"][0]["rows"][0]["cells"] == ["X", "X†"]
        doc, _ = build(back)
        assert not [i for i in validate(doc) if i["severity"] == "error"]


def test_bc_matching():
    lib = BCLibrary.default()
    assert lib.match("HGB").entry.name == "Hemoglobin Concentration in Blood"
    assert lib.match("glucose", specimen="urine").entry.name == "Glucose Presence in Urine"
    assert lib.match("glucose").entry.name == "Glucose Concentration in Serum or Plasma"
    m = lib.match("opsonophagocytic titre to serotype 22F")
    assert m.entry is None or m.review == "needs review"


def test_cosmos_loader():
    lib = BCLibrary()
    n = lib.load_cosmos_csv(ROOT / "tests" / "fixtures" / "cosmos_sample.csv")
    assert n > 0 and any(e.properties for e in lib.entries.values())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
