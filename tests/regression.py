"""Extraction regression check on the sample protocols in inputs/ (skips any that are missing).

    python tests/regression.py

Locks in the counts that were verified by hand / independent review, so changes to the extractor
for one protocol template cannot silently break another.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol_to_usdm.build import build  # noqa: E402
from protocol_to_usdm.pdf_extract import extract  # noqa: E402
from protocol_to_usdm.validate import validate  # noqa: E402

EXPECT = {
    # classic synopsis layout, 3 SoA tables, CCI-redacted
    "219659_protocol.pdf": {"soa": [22, 20, 17], "inc": 10, "exc": 18, "obj": 5, "ep": 21, "act": 23, "arms": 3},
    # TransCelerate CPT layout, one SoA over 4 pages with rotated headers
    "MK-1654-005_protocol.pdf": {"soa": [36], "inc": 10, "exc": 29, "obj": 12, "ep": 13, "act": 36, "arms": 5,
                                 "cols": 23, "phase": "Phase IIa Trial"},
}


def main():
    bad = 0
    for name, exp in EXPECT.items():
        p = ROOT / "inputs" / name
        if not p.exists():
            print(f"SKIP {name} (not in inputs/)")
            continue
        d, _ = extract(str(p))
        got = {"soa": [len(t["rows"]) for t in d["SoA"]],
               "inc": sum(1 for e in d["Eligibility"] if e["category"].startswith("Incl")),
               "exc": sum(1 for e in d["Eligibility"] if e["category"].startswith("Excl")),
               "obj": len(d["Objectives"]), "ep": len(d["Endpoints"]), "act": len(d["Activities"]), "arms": len(d["Arms"]),
               "cols": len(d["SoA"][0]["columns"]) if d["SoA"] else 0, "phase": d["Study"].get("phase")}
        doc, _ = build(d)
        errs = [i for i in validate(doc) if i["severity"] == "error"]
        diffs = {k: (v, got[k]) for k, v in exp.items() if got[k] != v}
        status = "PASS" if not diffs and not errs else "FAIL"
        bad += status == "FAIL"
        print(f"{status} {name}: {got}" + (f"\n     expected/got: {diffs}" if diffs else "") + (f"\n     {len(errs)} USDM errors" if errs else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
