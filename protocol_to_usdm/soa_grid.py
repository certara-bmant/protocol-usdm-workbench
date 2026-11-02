"""Template-agnostic Schedule of Activities table parser (ruled tables in PDFs).

Works from the drawn grid rather than from text layout, so it copes with:
  * merged header cells (epochs / visits spanning several columns) - per-band vertical rules
  * rotated (vertical) header text - character matrix orientation
  * superscript footnote markers - character size relative to the cell's body text
  * SoA tables continued over several pages (same column grid)
  * shaded 'continuous collection' bars, CCI redaction boxes, section/group rows
  * frequency tokens in cells (e.g. 'BID', 'tds') kept as 'X{BID}'

Output per table (same shape the extractor already uses):
  {"page", "title", "columns": [{epoch, number, day_text, notes, day, window_lower, window_upper}],
   "rows": [{label, footnotes, cells, continuous, redacted, group}], "footnotes": {marker: text}}
"""
from __future__ import annotations

import re
from collections import OrderedDict
from statistics import median

MARK_SYMBOLS = "†‡§*¶#"
SOA_TITLE = re.compile(r"Schedule of (?:Activities|Assessments|Events|Study Procedures)|Study Flow ?Chart|Time and Events", re.I)
DAY_PAT = re.compile(r"(?:\bday\b|\bd\s*-?\d+|\bweek\b|\bwk\s*\d+|^\s*-?\d+\s*(?:to\s*-?\d+)?\s*(?:\(|$))", re.I)
VISIT_PAT = re.compile(r"^\s*(?:visit|v)\s*\d+", re.I)


def _cluster(vals, tol=2.5):
    out = []
    for v in sorted(vals):
        if out and v - out[-1] <= tol:
            continue
        out.append(v)
    return out


# ------------------------------------------------------------------ text from characters
def _line_text(chars, rotated):
    """Join characters of one visual line, inserting spaces at gaps."""
    if not chars:
        return ""
    if rotated:
        ccw = (chars[0].get("matrix") or (0, 1))[1] > 0
        chars = sorted(chars, key=lambda c: -c["top"] if ccw else c["top"])
        out, prev = [], None
        for c in chars:
            if prev is not None:
                gap = (prev["top"] - c["bottom"]) if ccw else (c["top"] - prev["bottom"])
                if gap > 0.25 * max(c["size"], 1):
                    out.append(" ")
            out.append(c["text"])
            prev = c
        return "".join(out)
    chars = sorted(chars, key=lambda c: c["x0"])
    out, prev = [], None
    for c in chars:
        if prev is not None and c["x0"] - prev["x1"] > 0.2 * max(c["size"], 1):
            out.append(" ")
        out.append(c["text"])
        prev = c
    return "".join(out)


def cell_text(chars, small_ratio=0.8):
    """-> (body text, superscript/small text).  Handles rotated text and multi-line cells."""
    chars = [c for c in chars if c["text"].strip()]
    if not chars:
        return "", ""
    up = [c for c in chars if c.get("upright", True)]
    body_size = max([c["size"] for c in up] or [0])
    small = [c for c in up if c["size"] < small_ratio * body_size]
    big = [c for c in chars if c not in small]

    def lines(cs):
        up = [c for c in cs if c.get("upright", True)]
        rot = [c for c in cs if not c.get("upright", True)]
        res = []
        for group, rotated, key in ((up, False, "top"), (rot, True, "x0")):
            buckets = OrderedDict()
            for c in sorted(group, key=lambda c: c[key]):
                k = None
                for b in buckets:
                    if abs(b - c[key]) <= 0.6 * c["size"]:
                        k = b
                        break
                buckets.setdefault(k if k is not None else c[key], []).append(c)
            res += [_line_text(v, rotated) for v in buckets.values()]
        return " ".join(t.strip() for t in res if t.strip())
    return re.sub(r"\s+", " ", lines(big)).strip(), re.sub(r"\s+", "", lines(small))


PLAIN = (None, 0, 1, [0], [1], (0,), (1,), [0, 0, 0], (0, 0, 0), [1, 1, 1], (1, 1, 1), [0, 0, 0, 1], (0, 0, 0, 1),
         [0, 0, 0, 0], (0, 0, 0, 0))


def is_colored(r):
    """shaded 'continuous collection' bars are thin coloured rects, not table rules"""
    return r.get("non_stroking_color") not in PLAIN


# ------------------------------------------------------------------ grid of one page
class PageGrid:
    def __init__(self, page):
        self.page = page
        W = page.width
        segs = [(r["x0"], r["top"], r["bottom"]) for r in page.rects if r["width"] < 2.5 and r["height"] > 3]
        segs += [(l["x0"], l["top"], l["bottom"]) for l in page.lines if abs(l["x0"] - l["x1"]) < 1 and l["bottom"] - l["top"] > 3]
        self.vsegs = segs
        hw = {}
        for r in page.rects:
            if r["height"] < 2 and r["width"] > 2 and not is_colored(r):
                hw[round(r["top"])] = hw.get(round(r["top"]), 0) + r["width"]
        for l in page.lines:
            if abs(l["top"] - l["bottom"]) < 1:
                hw[round(l["top"])] = hw.get(round(l["top"]), 0) + abs(l["x1"] - l["x0"])
        self.h = _cluster([y for y, w in hw.items() if w > W * 0.45])
        self.bands = [(a, b) for a, b in zip(self.h, self.h[1:]) if b - a >= 4]
        counts = []
        for b in self.bands:
            counts.append(self.bounds(b))
        self.cols = max(counts, key=len) if counts else []
        self.ok = len(self.cols) >= 5 and len(self.bands) >= 4
        self.chars = page.chars

    def bounds(self, band, frac=None):
        """x of vertical rules crossing the whole band (or >= frac of its height)"""
        y0, y1 = band
        if frac is None:
            return _cluster([x for x, t, b in self.vsegs if t <= y0 + 2 and b >= y1 - 2])
        h = max(y1 - y0, 1)
        return _cluster([x for x, t, b in self.vsegs if (min(b, y1) - max(t, y0)) / h >= frac])

    def signature(self):
        return tuple(round(x / 3) for x in self.cols)

    def band_chars(self, band, x0=None, x1=None):
        y0, y1 = band
        out = []
        for c in self.chars:
            cy = (c["top"] + c["bottom"]) / 2
            cx = (c["x0"] + c["x1"]) / 2
            if y0 <= cy < y1 and (x0 is None or x0 <= cx < x1):
                out.append(c)
        return out


# ------------------------------------------------------------------ table assembly
def _classify(label, cells):
    nonempty = [c for c in cells if c]
    vis = sum(1 for c in nonempty if VISIT_PAT.match(c))
    day = sum(1 for c in nonempty if DAY_PAT.search(c))
    lab = label.lower()
    if re.search(r"visit\s*(number|no\b|#)", lab):
        return "number"
    if nonempty and vis >= 0.5 * len(nonempty):
        return "number"
    if re.search(r"\b(day|days|week|time|window)\b", lab) or (nonempty and day >= 0.6 * len(nonempty)):
        return "day"
    return "epoch"


def _parse_day(text):
    t = (text or "").replace("–", "-").replace("−", "-")
    m = re.search(r"(?:\bday\b|\bd)\s*(-?\d+)\s*(?:to|-)\s*(-?\d+)", t, re.I) or re.match(r"^\s*(-?\d+)\s*to\s*(-?\d+)", t)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if b < a:
            a, b = b, a
        return a, 0, b - a
    m = re.search(r"(?:\bday\b|\bd)\s*(-?\d+)", t, re.I) or re.match(r"^\s*(-?\d+)\b", t)
    if not m:
        return None, None, None
    d = int(m.group(1))
    pm = re.search(r"(?:±|\+/-)\s*(\d+)", t)
    plus = re.search(r"\(\s*\+\s*(\d+)", t)
    minus = re.search(r"\(\s*-\s*(\d+)", t)
    lo = int(pm.group(1)) if pm else (int(minus.group(1)) if minus else None)
    hi = int(pm.group(1)) if pm else (int(plus.group(1)) if plus else None)
    return d, lo, hi


def parse_soa(doc, log):
    """doc: ProtocolPDF (pages text + pdfplumber pdf).  Returns list of tables."""
    tables, cur = [], None
    for i, ptxt in enumerate(doc.pages):
        page = doc.pdf.pages[i]
        titled = bool(SOA_TITLE.search(ptxt))
        if not titled and cur is None:
            continue
        g = PageGrid(page)
        if not g.ok:
            if cur is not None:
                cur["tail_pages"].append(i)
                cur["fn_text"].append(doc.pages[i])
                if len(cur["tail_pages"]) > 3:
                    tables.append(cur)
                    cur = None
            continue
        title_line = next((l for l in ptxt.split("\n") if SOA_TITLE.search(l)), None)
        new_table_title = bool(title_line and re.match(r"^\s*(?:Table\s*\d+|\d+(?:\.\d+)*\s+)", title_line))
        same_grid = cur is not None and g.signature() == cur["sig"]
        if cur is not None and same_grid and not (new_table_title and re.match(r"^\s*Table\s*\d+", title_line or "")
                                                 and title_line.strip() != cur["title"]):
            _rows_from_page(g, cur, log, header_known=True)
            cur["pages"].append(i)
            cur["fn_text"].append(_below_grid(g))
            continue
        if cur is not None:
            tables.append(cur)
            cur = None
        if not titled:
            continue
        t = {"page": i + 1, "pages": [i], "tail_pages": [], "title": (title_line or "Schedule of Activities").strip(),
             "sig": g.signature(), "columns": None, "rows": [], "footnotes": {}, "_group": None, "_cci": 0,
             "fn_text": []}
        if _header_from_page(g, t) and _rows_from_page(g, t, log, header_known=False) is not None:
            t["fn_text"].append(_below_grid(g))
            cur = t
    if cur is not None:
        tables.append(cur)
    out = []
    for t in tables:
        nmarked = sum(1 for r in t["rows"] if any(r["cells"]))
        if not t["columns"] or nmarked < 3:
            continue
        txt = "\n".join(l for l in "\n".join(t["fn_text"]).split("\n") if doc._key(l) not in doc.boiler)
        # also footnotes continuing on the next non-grid pages (until a numbered heading)
        last = max(t["pages"] + t["tail_pages"])
        for j in range(last + 1, min(last + 3, len(doc.pages))):
            pt = doc.pages[j]
            if SOA_TITLE.search(pt) or re.search(r"(?m)^\d+(?:\.\d+)*\s+[A-Z][A-Za-z ,()/&-]{3,}$", pt[:400]):
                break
            txt += "\n" + pt
        t["footnotes"] = footnotes(txt)
        log.append(f"SoA '{t['title'][:70]}' (p{t['page']}{'-' + str(max(t['pages']) + 1) if len(t['pages']) > 1 else ''}): "
                   f"{len(t['columns'])} visit columns, {len(t['rows'])} rows, {len(t['footnotes'])} footnotes"
                   + (f", {t['_cci']} redacted (CCI) row(s) skipped" if t["_cci"] else ""))
        if t["_cci"]:
            log.append(f"REVIEW p{t['page']}: {t['_cci']} SoA row(s) redacted as 'CCI' - content unknown")
        unsched = [c["day_text"] for c in t["columns"] if c["day"] is None]
        if unsched:
            log.append(f"REVIEW SoA '{t['title'][:40]}': {len(unsched)} column(s) without a study day (unscheduled?): "
                       + "; ".join(u[:40] for u in unsched if u))
        for k in ("sig", "tail_pages", "_group", "_cci", "fn_text", "_header_end", "_hdr_n"):
            t.pop(k, None)
        out.append(t)
    return out


def _below_grid(g):
    """page text under the table's last horizontal rule (where SoA footnotes live)"""
    try:
        from .pdf_extract import fix
        y = getattr(g, "last_row_bottom", None)
        if y is None:
            y = g.h[-1] + 1 if g.h else 0
        return fix(g.page.within_bbox((0, y + 0.5, g.page.width, g.page.height)).extract_text(x_tolerance=1.5) or "")
    except Exception:
        return ""


def _has_mark(g, band, cols):
    for a, b in zip(cols[1:-1], cols[2:]):
        body, _ = cell_text(g.band_chars(band, a, b))
        if re.match(r"^[xX✓√●]", body):
            return True
    return False


def _merged_cell_text(g, band, col_bounds):
    """per global data column: text of the (possibly merged) cell covering it in this band"""
    bb = g.bounds(band)
    res = []
    for a, b in col_bounds:
        mid = (a + b) / 2
        lo = max([x for x in bb if x <= mid + 0.5] or [a])
        hi = min([x for x in bb if x > mid + 0.5] or [b])
        body, small = cell_text(g.band_chars(band, lo, hi))
        res.append((body, small, (lo, hi), (lo, hi) != (a, b)))
    return res


def _header_from_page(g, t):
    cols = g.cols
    label_right = cols[1]
    col_bounds = list(zip(cols[1:-1], cols[2:]))
    header = []
    for band in g.bands:
        if _has_mark(g, band, cols):
            break
        bb = g.bounds(band)
        label, _ = cell_text(g.band_chars(band, cols[0] - 1, label_right))
        if not any(abs(x - label_right) < 3 for x in bb) and header:
            break  # merged full-width row = first group row of the body
        header.append((band, label, _merged_cell_text(g, band, col_bounds)))
        if len(header) >= 8:
            break
    if not header:
        return False
    columns = [{"epoch": None, "number": None, "day_text": None, "notes": "", "label_text": None} for _ in col_bounds]
    top, bottom = header[0][0][0], header[-1][0][1]
    rot_text = []
    for a, b in col_bounds:
        rc = [c for c in g.band_chars((top, bottom), a, b) if not c.get("upright", True)]
        rot_text.append(cell_text(rc)[0])

    def upright_only(cells, band):  # header cells are re-read with upright characters only
        out = []
        for body, small, span, merged in cells:
            cs = [c for c in g.band_chars(band, span[0], span[1]) if c.get("upright", True)]
            b2, s2 = cell_text(cs)
            out.append((b2, s2, span, merged))
        return out
    header = [(band, label, upright_only(cells, band)) for band, label, cells in header]
    kinds = {}
    for band, label, cells in header:
        k = _classify(label, [c[0] for c in cells])
        if k in kinds:
            k = "extra" if k == "epoch" else k
        kinds.setdefault(k, band)
        for ci, (body, small, span, merged) in enumerate(cells):
            if not body:
                continue
            if k == "number":
                m = re.search(r"(?:visit|v)\s*(\d+)", body, re.I)
                columns[ci]["number"] = m.group(1) if m else (body if re.fullmatch(r"\d+", body) else None)
                columns[ci]["label_text"] = body
            elif k == "day":
                columns[ci]["day_text"] = ((columns[ci]["day_text"] or "") + " " + body).strip()
                columns[ci]["notes"] += "".join(ch for ch in body + small if ch in MARK_SYMBOLS)
                columns[ci]["notes"] += "".join(ch for ch in small if ch.isalpha() and ch.islower())
            elif k == "epoch":
                columns[ci]["epoch"] = re.sub(r"\s+", " ", body).strip()
                columns[ci]["notes"] += "".join(ch for ch in small if ch.isalpha() and ch.islower())
    for ci, rt in enumerate(rot_text):
        if rt:
            columns[ci]["day_text"] = ((columns[ci]["day_text"] or "") + " " + rt).strip()
    if "day" not in kinds and "number" not in kinds and not any(rot_text):
        return False
    # epoch text that sits in one cell but visually spans neighbours (unmerged rules): expand by text centre
    if "epoch" in kinds:
        band = kinds["epoch"]
        texts = {}
        for ci, (a, b) in enumerate(col_bounds):
            cs = [c for c in g.band_chars(band, a, b) if c.get("upright", True) and c["size"] >= 6]
            if cs:
                texts[ci] = (sum((c["x0"] + c["x1"]) / 2 for c in cs) / len(cs), columns[ci]["epoch"])
        for ci, (centre, name) in texts.items():
            best = (ci, ci)
            for lo in range(ci, -1, -1):
                if lo != ci and columns[lo]["epoch"]:
                    break
                for hi in range(ci, len(col_bounds)):
                    if hi != ci and columns[hi]["epoch"]:
                        break
                    mid = (col_bounds[lo][0] + col_bounds[hi][1]) / 2
                    if abs(mid - centre) < 12 and (hi - lo) > (best[1] - best[0]):
                        best = (lo, hi)
            for k in range(best[0], best[1] + 1):
                columns[k]["epoch"] = columns[k]["epoch"] or name
    for c in columns:
        c["epoch"] = re.sub(r"\s*\b[a-z]\b\s*$", "", c["epoch"] or "").strip() or None
        if c["day_text"]:
            sup = re.findall(r"(?<=\w)\s([a-z])\b(?=\s|$)", c["day_text"])
            for x in sup:
                if x not in c["notes"]:
                    c["notes"] += x
            c["day_text"] = re.sub(r"(?<=\w)\s[a-z]\b(?=\s|$)", "", c["day_text"])
        d, lo, hi = _parse_day(c["day_text"])
        c.update(day=d, window_lower=lo, window_upper=hi)
    # scheduled columns without an epoch inherit from the left neighbour
    for k in range(1, len(columns)):
        if not columns[k]["epoch"] and columns[k]["day"] is not None and columns[k - 1]["epoch"]:
            columns[k]["epoch"] = columns[k - 1]["epoch"]
    t["columns"] = columns
    t["_header_end"] = header[-1][0][1]
    t["_hdr_n"] = len(header)
    return True


def _rows_from_page(g, t, log, header_known):
    cols = g.cols
    label_right = cols[1]
    col_bounds = list(zip(cols[1:-1], cols[2:]))
    ncol = len(col_bounds)
    if ncol != len(t["columns"] or []):
        return None
    # skip this page's repeated header: everything above the first band with a mark or a group row
    start = None
    for band in g.bands:
        bb = g.bounds(band)
        if _has_mark(g, band, cols) or not any(abs(x - label_right) < 3 for x in bb):
            start = band[0]
            break
    if start is None:
        n = t.get("_hdr_n") or 0
        if n and len(g.bands) >= n:
            g.last_row_bottom = g.bands[n - 1][1]
        return t
    bars = [r for r in g.page.rects if r["height"] <= 1.6 and r["width"] > 15 and r["x0"] >= label_right - 40
            and is_colored(r) and r["width"] < (cols[-1] - cols[0]) * 0.95]
    cci_tops = [w["top"] for w in g.page.extract_words() if w["text"] == "CCI"]
    for band in g.bands:
        if band[0] < start:
            continue
        subbands = [band]
        cuts = [y for y in cci_tops if band[0] <= y < band[1]]
        if cuts:
            edges = [band[0]]
            for y in sorted(cuts):
                edges += [y - 1, y + 12]
            edges.append(band[1])
            subbands = [(a, z) for a, z in zip(edges[::2], edges[1::2]) if z - a > 4]
            t["_cci"] += len(cuts)
        for sb in subbands:
            _row(g, sb, t, cols, col_bounds, label_right, bars)
    if getattr(g, "last_row_bottom", None) is None:
        n = t.get("_hdr_n") or 0
        if n and len(g.bands) >= n:
            g.last_row_bottom = g.bands[n - 1][1]
    return t


def _row(g, band, t, cols, col_bounds, label_right, bars):
    bb = g.bounds(band, frac=0.5)
    lab_chars = [c for c in g.band_chars(band, cols[0] - 1, label_right) if c["text"] != "CCI"]
    label, small = cell_text(lab_chars)
    label = re.sub(r"\bCCI\b", "", label).strip()
    half = cols[1] + (cols[-1] - cols[1]) / 2
    merged_label = not any(cols[0] + 3 < x < half for x in bb)
    has_bar = any(band[0] <= (r["top"] + r["bottom"]) / 2 < band[1] for r in bars)
    if merged_label and not has_bar:
        whole, _ = cell_text(g.band_chars(band, cols[0] - 1, cols[-1] + 1))
        if whole and not re.match(r"^[xX]\b", whole):
            if len(whole) < 120 and not re.match(r"^[a-z]\.\s", whole):
                t["_group"] = whole
            return
    if not label or re.match(r"^(Abbreviations?|AE:|Footnotes|Note)", label):
        return
    fns = [ch for ch in small if ch.isalpha() and ch.islower()] + [ch for ch in label if ch in MARK_SYMBOLS]
    label = "".join(ch for ch in label if ch not in MARK_SYMBOLS).strip().rstrip(",")
    # merged cells in this row (e.g. 'Xc' centred across Day 27/Day 28 = either day): read once, apply to all
    spans = []
    inner = [x for x in bb if cols[1] - 1 <= x <= cols[-1] + 1]
    for lo, hi in zip(inner, inner[1:]):
        covered = [ci for ci, (a, b) in enumerate(col_bounds) if lo - 1 <= (a + b) / 2 <= hi + 1]
        if len(covered) > 1:
            spans.append((lo, hi, covered))
    span_of = {ci: (lo, hi) for lo, hi, cov in spans for ci in cov}
    cells = []
    for ci, (a, b) in enumerate(col_bounds):
        if ci in span_of:
            a, b = span_of[ci]
        body, sm = cell_text(g.band_chars(band, a, b))
        body = body.strip()
        if re.match(r"^[xX✓√●]", body):
            rest = body[1:]
            freq = re.findall(r"[A-Za-z]{2,}", rest)
            rest = re.sub(r"[A-Za-z]{2,}", "", rest)
            marks = "".join(ch for ch in rest + sm if ch.isalpha() or ch in MARK_SYMBOLS)
            cells.append("X" + ("{" + " ".join(freq) + "}" if freq else "") + marks)
        elif body and re.fullmatch(r"[A-Za-z][A-Za-z ./-]{1,12}", body):
            cells.append("X{" + body + "}" + "".join(ch for ch in sm if ch.isalpha() or ch in MARK_SYMBOLS))
        else:
            cells.append("")
    if merged_label and not any(cells) and not any(
            band[0] <= (r["top"] + r["bottom"]) / 2 < band[1] for r in bars):
        whole, _ = cell_text(g.band_chars(band, cols[0] - 1, cols[-1] + 1))
        if whole:
            t["_group"] = whole
        return
    m = re.search(r"\s([a-z])$", label)
    if m:
        fns.append(m.group(1))
        label = label[:m.start()].strip()
    cont = False
    for r in bars:
        mid = (r["top"] + r["bottom"]) / 2
        if band[0] <= mid < band[1]:
            for ci, (a, z) in enumerate(col_bounds):
                if r["x0"] <= (a + z) / 2 <= r["x1"] and not cells[ci]:
                    cells[ci] = "X"
                    cont = True
    if not any(cells) and not cont:
        if t["rows"] and re.fullmatch(r"[a-z](?:\s*,\s*[a-z])*", label):  # stray superscript line
            t["rows"][-1]["footnotes"] += [x for x in re.findall(r"[a-z]", label)]
        elif t["rows"] and label[:1] in "(-" or label[:1].islower():  # wrapped label continuation
            prev = t["rows"][-1]
            prev["label"] = (prev["label"] + " " + label).strip()
            prev["footnotes"] += fns
        else:  # unmarked title-case row = section / group heading
            t["_group"] = label
        return
    g.last_row_bottom = max(getattr(g, "last_row_bottom", 0) or 0, band[1])
    t["rows"].append({"label": label, "footnotes": list(OrderedDict.fromkeys(fns)), "cells": cells,
                      "continuous": cont, "redacted": False, "group": t["_group"]})


def footnotes(text):
    notes, cur = OrderedDict(), None
    for line in text.split("\n"):
        if notes and (re.match(r"^\s*\d+(?:\.\d+)*\s+[A-Z][A-Z ,()/&'-]{3,}$", line) or re.match(r"^\s*\d+\.\d+(?:\.\d+)*\s+[A-Z][a-z]", line)):
            break  # next numbered section heading - footnotes are over
        m = re.match(r"^\s*([†‡§*¶])\s+(.*)$", line) or re.match(r"^\s*([a-z]{1,2})\.\s+(.*)$", line)
        if m:
            cur = m.group(1)
            notes[cur] = m.group(2).strip()
        elif cur and line.strip() and not re.match(r"^(Table|Flow Chart|Sponsor:|PRODUCT:|PROTOCOL/|\d+\s+[A-Z]{4,})", line.strip()):
            if re.match(r"^[A-Z][A-Za-z ]+:", line.strip()) and len(line) < 40:
                cur = None
                continue
            notes[cur] += " " + line.strip()
    out = {}
    for k, v in notes.items():
        v = re.sub(r"\s*Footnotes (?:continued|appear) on next page.*$", "", re.sub(r"\s+", " ", v).strip())
        if v:
            out[k] = v
    return out
