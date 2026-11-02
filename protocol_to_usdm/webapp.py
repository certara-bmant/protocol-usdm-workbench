"""Local web front end (standard library only).

    python -m protocol_to_usdm serve [--port 8765] [--cosmos bcs.csv] [--evs] [--no-browser]

Serves web/index.html and a small JSON API.  Everything runs on this machine; uploaded protocols are
processed entirely in memory (no temporary files).
"""
from __future__ import annotations

import io
import json
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .bc_library import BCLibrary
from .ct import Terminology
from .template import KV_SHEETS, LIST_SHEETS, read_workbook, write_workbook
from .validate import summarize, validate

WEB = Path(__file__).parent / "web"
SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "219659_extracted.xlsx"


class App:
    def __init__(self, lib: BCLibrary):
        self.lib = lib
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ API
    def schema(self, _q, _b):
        ct = Terminology()
        fields = {f for s in KV_SHEETS.values() for _, _, f in s if f} | {f for s in LIST_SHEETS.values() for _, f in s if f}
        return {"version": __version__,
                "kv_sheets": {k: [[a, h, f] for a, h, f in v] for k, v in KV_SHEETS.items()},
                "list_sheets": {k: [[c, f] for c, f in v] for k, v in LIST_SHEETS.items()},
                "ct": {f: ct.decodes(f) for f in sorted(fields)},
                "sample": SAMPLE.exists(),
                "library": {"bcs": len(self.lib.entries), "surrogates": len(self.lib.surrogates),
                            "evs_online": self.lib.evs_online}}

    def extract(self, q, body):
        from .pdf_extract import extract
        with self.lock:  # in memory: no temp file for Windows to lock
            data, report = extract(bytes(body), self.lib)
        return {"data": _jsonable(data), "report": report, "source": q.get("name", ["protocol.pdf"])[0]}

    def import_xlsx(self, q, body):
        data = read_workbook(io.BytesIO(body))
        return {"data": _jsonable(data), "report": "imported workbook", "source": q.get("name", ["workbook.xlsx"])[0]}

    def import_json(self, q, body):
        from .export import export
        return {"data": _jsonable(export(json.loads(body.decode("utf-8")))), "report": "imported USDM JSON",
                "source": q.get("name", ["usdm.json"])[0]}

    def sample(self, _q, _b):
        return {"data": _jsonable(read_workbook(SAMPLE)), "report": (SAMPLE.parent / "219659_extraction_report.txt").read_text(
            encoding="utf-8") if (SAMPLE.parent / "219659_extraction_report.txt").exists() else "", "source": SAMPLE.name}

    def build(self, _q, body):
        from .build import BuildError, build
        data = json.loads(body.decode("utf-8"))
        try:
            doc, warns = build(data, self.lib)
        except (BuildError, ValueError, KeyError) as e:
            return {"ok": False, "error": str(e)}
        issues = validate(doc)
        v = doc["study"]["versions"][0]
        d = v["studyDesigns"][0]
        stats = {"activities": len(d["activities"]), "encounters": len(d["encounters"]), "timelines": len(d["scheduleTimelines"]),
                 "biomedicalConcepts": len(v["biomedicalConcepts"]), "surrogates": len(v["bcSurrogates"]),
                 "criteria": len(d["eligibilityCriteria"]), "objectives": len(d["objectives"]),
                 "bytes": len(json.dumps(doc))}
        return {"ok": True, "summary": summarize(issues), "issues": issues, "warnings": warns, "stats": stats, "usdm": doc}

    def bc_search(self, q, _b):
        return {"results": self.lib.search(q.get("q", [""])[0], (q.get("specimen", [None])[0] or None))}

    # ------------------------------------------------------------------ binary downloads
    def download_xlsx(self, body):
        data = json.loads(body.decode("utf-8"))
        buf = io.BytesIO()
        write_workbook(data, buf, Terminology())
        return buf.getvalue()


def _jsonable(o):
    return json.loads(json.dumps(o, default=str))


def make_handler(app: App):
    routes = {("GET", "/api/schema"): app.schema, ("GET", "/api/sample"): app.sample,
              ("GET", "/api/bc/search"): app.bc_search, ("POST", "/api/extract"): app.extract,
              ("POST", "/api/import/xlsx"): app.import_xlsx, ("POST", "/api/import/json"): app.import_json,
              ("POST", "/api/build"): app.build}

    class Handler(BaseHTTPRequestHandler):
        server_version = f"protocol_to_usdm/{__version__}"

        def log_message(self, fmt, *args):  # quieter console
            if not str(args[0]).startswith(("GET /api/bc/search",)):
                super().log_message(fmt, *args)

        def _send(self, code, payload, ctype="application/json", headers=None):
            raw = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 200 * 1024 * 1024:
                raise ValueError("upload too large")
            return self.rfile.read(n) if n else b""

        def _dispatch(self, method):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if method == "GET" and u.path in ("/", "/index.html"):
                    return self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
                if method == "POST" and u.path == "/api/download/xlsx":
                    name = q.get("name", ["study.xlsx"])[0]
                    return self._send(200, app.download_xlsx(self._body()),
                                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                      {"Content-Disposition": f'attachment; filename="{name}"'})
                fn = routes.get((method, u.path))
                if fn is None:
                    return self._send(404, {"error": f"no route {method} {u.path}"})
                return self._send(200, fn(q, self._body() if method == "POST" else b""))
            except Exception as e:  # report to the UI instead of dropping the connection
                traceback.print_exc()
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

    return Handler


def serve(port=8765, cosmos=None, evs=False, open_browser=True, host="127.0.0.1"):
    lib = BCLibrary.default(evs_online=evs)
    if cosmos:
        print(f"loaded {lib.load_cosmos_csv(cosmos)} COSMoS BCs")
    httpd = ThreadingHTTPServer((host, port), make_handler(App(lib)))
    url = f"http://{host}:{port}/"
    print(f"protocol_to_usdm UI running at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
