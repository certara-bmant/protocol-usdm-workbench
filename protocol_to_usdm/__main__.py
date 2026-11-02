"""Command line interface.

  python -m protocol_to_usdm template  <out.xlsx>                      blank template
  python -m protocol_to_usdm extract   <protocol.pdf> <out.xlsx> [--cosmos bcs.csv] [--evs]
  python -m protocol_to_usdm build     <workbook.xlsx> <out.json> [--cosmos bcs.csv]
  python -m protocol_to_usdm validate  <usdm.json>
  python -m protocol_to_usdm export    <usdm.json> <out.xlsx>          USDM JSON -> editable workbook
  python -m protocol_to_usdm serve     [--port 8765] [--cosmos bcs.csv] [--evs] [--no-browser]   web UI
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 10):  # fail with a clear message instead of a syntax/type error deep inside
    sys.exit(f"protocol_to_usdm needs Python 3.10 or newer; this is {sys.version.split()[0]} at {sys.executable}.\n"
             "On Windows use run_ui.bat (it picks a suitable Python), or run e.g.  py -3.11 -m protocol_to_usdm serve")

import argparse  # noqa: E402
import json  # noqa: E402

from .bc_library import BCLibrary
from .ct import Terminology
from .template import read_workbook, write_workbook
from .validate import summarize, validate


def _lib(args):
    lib = BCLibrary.default(evs_online=getattr(args, "evs", False))
    if getattr(args, "cosmos", None):
        n = lib.load_cosmos_csv(args.cosmos)
        print(f"loaded {n} COSMoS BCs", file=sys.stderr)
    return lib


def main(argv=None):
    ap = argparse.ArgumentParser(prog="protocol_to_usdm")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("template"); p.add_argument("out")
    p = sub.add_parser("extract"); p.add_argument("pdf"); p.add_argument("out")
    p.add_argument("--cosmos"); p.add_argument("--evs", action="store_true")
    p = sub.add_parser("build"); p.add_argument("xlsx"); p.add_argument("out"); p.add_argument("--cosmos")
    p = sub.add_parser("validate"); p.add_argument("json"); p.add_argument("--all", action="store_true")
    p = sub.add_parser("export"); p.add_argument("json"); p.add_argument("out")
    p = sub.add_parser("serve"); p.add_argument("--port", type=int, default=8765); p.add_argument("--cosmos")
    p.add_argument("--evs", action="store_true"); p.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        from .webapp import serve
        serve(args.port, args.cosmos, args.evs, not args.no_browser)
        return 0

    if args.cmd == "template":
        write_workbook({}, args.out, Terminology())
    elif args.cmd == "extract":
        from .pdf_extract import extract
        data, report = extract(args.pdf, _lib(args))
        write_workbook(data, args.out, Terminology())
        print(report)
    elif args.cmd == "build":
        from .build import build
        doc, warns = build(read_workbook(args.xlsx), _lib(args))
        json.dump(doc, open(args.out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        for w in warns:
            print("warning:", w, file=sys.stderr)
        iss = validate(doc)
        print(f"wrote {args.out}; validation: {summarize(iss)}")
        for i in iss:
            if i["severity"] == "error":
                print(" ", i)
        return 1 if any(i["severity"] == "error" for i in iss) else 0
    elif args.cmd == "validate":
        iss = validate(json.load(open(args.json, encoding="utf-8")))
        print(summarize(iss))
        for i in iss if args.all else [i for i in iss if i["severity"] == "error"]:
            print(" ", i)
        return 1 if any(i["severity"] == "error" for i in iss) else 0
    elif args.cmd == "export":
        from .export import export
        write_workbook(export(json.load(open(args.json, encoding="utf-8"))), args.out, Terminology())
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
