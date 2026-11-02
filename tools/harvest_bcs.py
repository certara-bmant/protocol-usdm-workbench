"""Harvest biomedical concepts (with full property structure) from an existing USDM v4 JSON file
into the local BC library format used by protocol_to_usdm.bc_library.

    python tools/harvest_bcs.py CTX101-001_USDM_v4.json protocol_to_usdm/data/bc_library_seed.json

Ids are stripped so properties can be re-instantiated in any study.
"""
import json
import sys


def strip_ids(o):
    if isinstance(o, dict):
        return {k: strip_ids(v) for k, v in o.items() if k != "id"}
    if isinstance(o, list):
        return [strip_ids(x) for x in o]
    return o


def main(src, out):
    d = json.load(open(src, encoding="utf-8"))
    v = d["study"]["versions"][0]
    cat_of = {}
    for c in v.get("bcCategories", []):
        for m in c.get("memberIds", []):
            cat_of[m] = c["name"]
    lib = []
    for bc in v.get("biomedicalConcepts", []):
        code = (bc.get("code") or {}).get("standardCode", {})
        lib.append({
            "ncit_code": code.get("code") or bc["reference"].split("/")[-1],
            "name": bc["name"],
            "label": bc.get("label"),
            "decode": code.get("decode"),
            "synonyms": bc.get("synonyms", []),
            "reference": bc["reference"],
            "category": cat_of.get(bc["id"]),
            "source": "CDISC BC (harvested from " + src.split("/")[-1] + ")",
            "properties": strip_ids(bc.get("properties", [])),
            "extensions": [{"url": e["url"], "valueString": e.get("valueString")} for e in bc.get("extensionAttributes", [])],
        })
    sur = [{"name": s["name"], "description": s.get("description"), "reference": s.get("reference")}
           for s in v.get("bcSurrogates", [])]
    json.dump({"concepts": lib, "surrogates": sur}, open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"{len(lib)} BCs, {len(sur)} surrogates -> {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
