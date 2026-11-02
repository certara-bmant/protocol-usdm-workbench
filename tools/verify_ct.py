"""Check every C-code used by the toolkit (built-in CT table, BC seed library, EVS cache, and optionally a
generated USDM JSON) against the live NCI EVS REST API.  Flags codes that don't exist, are retired, or
whose NCIt preferred name looks unrelated to our decode.  Needs internet access.

    python tools/verify_ct.py [usdm.json]
"""
import json
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protocol_to_usdm.bc_library import BCLibrary, DATA  # noqa: E402
from protocol_to_usdm.ct import CT  # noqa: E402


def codes_from_usdm(path):
    out = {}

    def walk(o):
        if isinstance(o, dict):
            if o.get("instanceType") == "Code" and o.get("codeSystem") == "http://www.cdisc.org":
                out.setdefault(o["code"], o.get("decode"))
            [walk(v) for v in o.values()]
        elif isinstance(o, list):
            [walk(v) for v in o]
    walk(json.load(open(path, encoding="utf-8")))
    return out


def main(argv):
    todo = {}
    for field, rows in CT.items():
        for code, decode, _ in rows:
            todo.setdefault(code, decode)
    seed = json.loads((DATA / "bc_library_seed.json").read_text(encoding="utf-8"))
    for c in seed["concepts"]:
        todo.setdefault(c["ncit_code"], c.get("decode") or c["name"])
    for v in json.loads((DATA / "evs_cache.json").read_text(encoding="utf-8")).values():
        todo.setdefault(v["code"], v["name"])
    if argv:
        todo.update({k: v for k, v in codes_from_usdm(argv[0]).items() if k not in todo})
    bad = 0
    for code, decode in sorted(todo.items()):
        d = BCLibrary.evs_verify(code)
        if not d:
            print(f"MISSING/RETIRED  {code}  (we call it '{decode}')")
            bad += 1
        else:
            sim = SequenceMatcher(None, d["name"].lower(), str(decode).lower()).ratio()
            flag = "  CHECK NAME" if sim < 0.5 else ""
            print(f"ok  {code:9} {d['name'][:50]:50} | ours: {str(decode)[:40]}{flag}")
        time.sleep(0.2)
    print(f"\n{len(todo)} codes checked, {bad} missing/retired")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
