"""Round-trip test: USDM JSON -> Excel -> USDM JSON, then compare content (ids are regenerated, so the
comparison is on names, texts, codes, schedule matrix and BC structure).

    python tests/roundtrip.py <usdm.json> [workdir]
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from protocol_to_usdm.build import build  # noqa: E402
from protocol_to_usdm.ct import Terminology  # noqa: E402
from protocol_to_usdm.export import export  # noqa: E402
from protocol_to_usdm.template import read_workbook, write_workbook  # noqa: E402
from protocol_to_usdm.validate import summarize, validate  # noqa: E402


def fingerprint(doc):
    v = doc["study"]["versions"][0]
    d = v["studyDesigns"][0]
    ids = {}

    def walk(o):
        if isinstance(o, dict):
            if "id" in o and "instanceType" in o:
                ids[o["id"]] = o
            [walk(x) for x in o.values()]
        elif isinstance(o, list):
            [walk(x) for x in o]
    walk(doc)
    nm = lambda i: ids[i].get("name") if i in ids else None  # noqa: E731
    code = lambda c: (c or {}).get("standardCode", c or {}).get("code")  # noqa: E731
    fp = {
        "titles": sorted((t["type"]["code"], t["text"]) for t in v["titles"]),
        "identifiers": sorted((i["text"], nm(i["scopeId"])) for i in v["studyIdentifiers"]),
        "organizations": sorted((o["name"], o["type"]["code"]) for o in v["organizations"]),
        "design codes": (code(d.get("studyType")), code(d.get("studyPhase")), code(d.get("blindingSchema")), code(d.get("model")),
                         sorted(code(c) for c in d.get("characteristics", [])), sorted(code(c) for c in d.get("subTypes", []))),
        "arms": sorted((a["name"], a["type"]["code"]) for a in d["arms"]),
        "epochs": [(e["name"], e["type"]["code"]) for e in d["epochs"]],
        "cells": sorted((nm(c["armId"]), nm(c["epochId"]), tuple(nm(x) for x in c["elementIds"])) for c in d["studyCells"]),
        "interventions": sorted((i["name"], i["role"]["code"], tuple((a["name"], (a.get("dose") or {}).get("value"),
                                 code((a.get("dose") or {}).get("unit")), code(a.get("route"))) for a in i["administrations"]))
                                for i in v["studyInterventions"]),
        "objectives": sorted((o["name"], o["level"]["code"], o["text"], tuple(sorted((e["name"], e["level"]["code"], e["text"])
                              for e in o["endpoints"]))) for o in d["objectives"]),
        "eligibility": [(c["identifier"], c["category"]["code"], ids[c["criterionItemId"]]["text"]) for c in d["eligibilityCriteria"]],
        "population": (d["population"]["name"], (d["population"].get("plannedEnrollmentNumber") or {}).get("value"),
                       sorted(code(c) for c in d["population"].get("plannedSex", [])), len(d["population"].get("cohorts", []))),
        "encounters": [(e["name"], tuple(code(c) for c in e.get("contactModes", []))) for e in d["encounters"]],
        "activities": [(a["name"], tuple(sorted(nm(x) for x in a["biomedicalConceptIds"])),
                        tuple(sorted(nm(x) for x in a["bcSurrogateIds"]))) for a in d["activities"]],
        "bcs": sorted((b["name"], code(b["code"]), len(b["properties"]),
                       sum(len(p.get("responseCodes", [])) for p in b["properties"])) for b in v["biomedicalConcepts"]),
        "bc categories": sorted((c["name"], len(c["memberIds"])) for c in v["bcCategories"]),
        "surrogates": sorted(s["name"] for s in v["bcSurrogates"]),
        "schedule": sorted((t["name"], i["name"].split("_", 1)[-1] if False else nm(i.get("encounterId")) or i.get("label"),
                            tuple(sorted(nm(a) for a in i["activityIds"])))
                           for t in d["scheduleTimelines"] for i in t["instances"]),
        "timings": sorted((t["name"], tm["type"]["code"], tm["value"], tm.get("windowLower"), tm.get("windowUpper"))
                          for t in d["scheduleTimelines"] for tm in t["timings"]),
    }
    return fp


def main(src, work=None):
    work = Path(work or tempfile.mkdtemp())
    work.mkdir(parents=True, exist_ok=True)
    orig = json.load(open(src, encoding="utf-8"))
    xlsx = work / "roundtrip.xlsx"
    write_workbook(export(orig), xlsx, Terminology())
    rebuilt, warns = build(read_workbook(xlsx))
    (work / "roundtrip.json").write_text(json.dumps(rebuilt, indent=1), encoding="utf-8")
    a, b = fingerprint(orig), fingerprint(rebuilt)
    ok = 0
    print(f"validation of rebuilt file: {summarize(validate(rebuilt))}")
    for k in a:
        if a[k] == b[k]:
            ok += 1
            print(f"  PASS  {k}")
        else:
            print(f"  DIFF  {k}")
            sa, sb = a[k], b[k]
            if isinstance(sa, list):
                print("        only in original:", [x for x in sa if x not in sb][:4])
                print("        only in rebuilt :", [x for x in sb if x not in sa][:4])
            else:
                print("        original:", sa, "\n        rebuilt :", sb)
    print(f"{ok}/{len(a)} sections identical")
    return 0 if ok == len(a) else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
