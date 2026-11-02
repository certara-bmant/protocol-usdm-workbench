"""Protocol PDF -> StudyData (Excel template form).

Deterministic, layout-aware extraction (pdfplumber):
  * text clean-up: font-glyph repair (Symbol-font µ, bullets), repeated header/footer removal
  * synopsis fields, identifiers, titles, objectives, endpoints, eligibility, abbreviations, narrative
  * Schedule of Assessments tables: grid from ruling lines, X marks + footnote markers per cell,
    shaded bars (continuous collection) mapped to columns, footnotes -> Conditions
  * lab appendix table -> panel expansion for BC matching
  * tiered BC matching (bc_library.BCLibrary) with provenance on every mapping

Heuristics are tuned on ICH-E6-style sponsor protocols (synopsis + SoA tables).  Anything the rules
cannot find is left blank and reported, so a reviewer (or an LLM pass) can fill it in the workbook.
"""
from __future__ import annotations

import re
from collections import Counter, OrderedDict

import pdfplumber

from .bc_library import BCLibrary, norm

GLYPHS = {"": "µ", "": "•", "": "≥", "": "≤", "": "±", "∙": "•", "": "•"}
MARK_SYMBOLS = "†‡§*¶#"


def fix(s: str) -> str:
    for k, v in GLYPHS.items():
        s = s.replace(k, v)
    return s


# ============================================================================================ document
class ProtocolPDF:
    def __init__(self, path):
        """path: file path, bytes, or a binary file object (bytes avoid temp files - Windows locks open files)"""
        self.path = path if isinstance(path, str) else "<in-memory>"
        if isinstance(path, (bytes, bytearray)):
            import io
            path = io.BytesIO(path)
        self.pdf = pdfplumber.open(path)
        self.meta = self.pdf.metadata or {}
        raw = [fix(p.extract_text(x_tolerance=1.5) or "") for p in self.pdf.pages]
        self.boiler = self._boilerplate(raw)
        self.pages = [self._repair_first_line("\n".join(l for l in t.split("\n") if self._key(l) not in self.boiler)) for t in raw]
        self.text = "\n".join(self.pages)

    @staticmethod
    def _repair_first_line(t):
        """Some PDF producers split the first glyph of a page from its word ('M ale', '1 3.'); re-join it."""
        lines = t.split("\n")
        for i, l in enumerate(lines):
            if l.strip():
                l = re.sub(r"^(\s*)([B-HJ-Zb-z]) ([a-z]{2,})", r"\1\2\3", l)
                lines[i] = re.sub(r"^(\s*)(\d) (\d)([.)]\s)", r"\1\2\3\4", l)
                break
        return "\n".join(lines)

    def close(self):
        try:
            self.pdf.close()
        except Exception:
            pass

    @staticmethod
    def _key(line):
        return re.sub(r"\d+", "#", line.strip())

    def _boilerplate(self, raw):
        c = Counter()
        for t in raw:
            c.update({self._key(l) for l in t.split("\n") if l.strip()})
        n = len(raw)
        return {k for k, v in c.items() if v >= max(3, 0.3 * n) and len(k) < 160}

    def words(self, i):
        return [dict(w, text=fix(w["text"])) for w in self.pdf.pages[i].extract_words(x_tolerance=1.5, extra_attrs=["size"])]

    # ----------------------------------------------------------------------------- text helpers
    def block(self, start, ends, text=None, flags=re.M):
        """text between a start regex and the first of several end regexes"""
        text = text if text is not None else self.text
        m = re.search(start, text, flags)
        if not m:
            return None
        rest = text[m.end():]
        cut = len(rest)
        for e in ends:
            m2 = re.search(e, rest, flags)
            if m2 and m2.start() < cut:
                cut = m2.start()
        return rest[:cut].strip()

    def field(self, label, text=None):
        """'Label: value' on one line (synopsis style); value may continue on following lines"""
        m = re.search(rf"(?m)^{label}\s*:?\s*(.*)$", text or self.text)
        return m.group(1).strip() if m else None


def oneline(s):
    return re.sub(r"\s+", " ", s or "").strip()


# ============================================================================================ extract
def extract(path, lib: BCLibrary | None = None):
    lib = lib or BCLibrary.default()
    doc = ProtocolPDF(path)
    try:
        return _extract(doc, lib)
    finally:
        doc.close()


def _extract(doc, lib):
    log: list[str] = []
    data: dict = {"Study": {}, "Population": {}}
    synopsis = doc.block(r"^(?:IV\.?\s+)?SYNOPSIS\s*$", [r"^(?:V\.?\s+)?FLOW CHART", r"^1\s+INTRODUCTION\s*$"]) or doc.text

    _identity(doc, synopsis, data, log)
    _objectives_endpoints(synopsis, data, log)
    _eligibility(synopsis, data, log)
    _abbreviations(doc, data, log)
    _narrative(doc, data, log)
    from . import generic
    from .soa_grid import parse_soa
    tables = parse_soa(doc, log)
    panels = _lab_panels(doc, log) or generic.lab_panels(doc, lib, log)
    _design(doc, synopsis, tables, data, log)
    generic.fill_gaps(doc, data, tables, log)
    if data.get("Eligibility"):
        log[:] = [l for l in log if not re.search(r"(Inclusion|Exclusion) Criteria: block not found|eligibility criteria: 0 inclusion", l)]
    if data.get("Objectives"):
        log[:] = [l for l in log if not l.startswith("objectives: 0,")]
    _schedule(tables, panels, lib, data, log)
    report = _report(data, log)
    return data, report


# -------------------------------------------------------------------------------- identity / design
def _identity(doc, syn, data, log):
    S = data["Study"]
    p1 = doc.pages[0] + "\n" + (doc.pages[1] if len(doc.pages) > 1 else "")
    title = doc.block(r"Official Title of Study\s*:\s*", [r"^\s*NCT number", r"^\s*Other Identifier", r"^\s*Date of"], p1) \
        or doc.block(r"^Title of Study\s*:?\s*$", [r"^Planned Study Period", r"^Study Objective"], syn)
    titles = []
    if title:
        titles.append({"type": "Official Study Title", "text": oneline(title)})
    proto_no = doc.field(r"(?:Protocol Number|ISN/Protocol)", syn) or ""
    proto_no = re.sub(r"^.*?(\d{3,}-[A-Z]{2}-\d+).*$", r"\1", proto_no) if proto_no else None
    m = re.search(r"Protocol Number:\s*\n.*?(\S+-CL-\S+)", syn)
    proto_no = (m.group(1) if m else proto_no) or doc.field(r"Other Identifier", p1)
    sponsor_full = None
    m = re.search(r"(?m)^Sponsor:\s*(?:Protocol Number:)?\s*\n(.+?)(?:\s{2,}|\s+\d{3,}-CL-|\n)", syn)
    if m:
        sponsor_full = oneline(m.group(1))
    if not sponsor_full:
        m = re.search(r"(?m)^Sponsor:\s*\n(.+)$", p1)
        sponsor_full = oneline(m.group(1)) if m else None
    sponsor_full = re.sub(r"\s*\d{4}-CL-\d+\s*$", "", sponsor_full or "Sponsor")
    S["sponsor"] = sponsor_full
    orgs = [{"name": sponsor_full, "label": "Sponsor", "type": "Drug Company", "identifier_scheme": "N/A",
             "identifier": sponsor_full}]
    ids = []
    if proto_no:
        ids.append({"text": proto_no, "organization": sponsor_full})
    nct = re.search(r"\bNCT\d{8}\b", doc.text)
    if nct:
        orgs.append({"name": "ClinicalTrials.gov", "type": "Clinical Study Registry", "identifier_scheme": "N/A",
                     "identifier": "ClinicalTrials.gov"})
        ids.append({"text": nct.group(0), "organization": "ClinicalTrials.gov"})
    ind = re.search(r"(?m)^IND\s+(\d{4,6})\s*$", p1)
    if ind:
        orgs.append({"name": "US FDA", "label": "Food and Drug Administration", "type": "Government Institute",
                     "identifier_scheme": "N/A", "identifier": "FDA"})
        ids.append({"text": "IND " + ind.group(1), "organization": "US FDA"})
    sid = re.search(r"(?m)^Study ID:\s*(\S+)", p1)
    if sid:
        pub = doc.meta.get("Author") or "Document publisher"
        orgs.append({"name": pub, "label": "Document publisher (from PDF metadata) - review", "type": "Drug Company",
                     "identifier_scheme": "N/A", "identifier": pub})
        ids.append({"text": sid.group(1), "organization": pub})
        log.append(f"REVIEW identifier 'Study ID {sid.group(1)}' scoped to '{pub}' (taken from PDF metadata Author)")
    data["Organizations"], data["Identifiers"], data["Titles"] = orgs, ids, titles

    ver = re.search(r"(?m)^Version\s+(\d+(?:\.\d+)*)\s*$", p1)
    S["version"] = ver.group(1) if ver else None
    amd = re.search(r"(?m)^Incorporating\s+(.+?)(?:\s*\[.*\])?$", p1)
    S["version_rationale"] = oneline(amd.group(1)) if amd else "Original protocol"
    dt = re.search(r"Date and Version No of Protocol Synopsis:\s*(\d{1,2} \w{3} \d{4})", syn) or \
        re.search(r"(?m)^(\d{1,2} \w{3} \d{4})\s*$", p1)
    if dt:
        from datetime import datetime
        try:
            S["approval_date"] = datetime.strptime(dt.group(1), "%d %b %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
    acr = re.search(r"Name of Study Drug:.*\n(\S+)", syn)
    drug = acr.group(1) if acr else None
    S["name"] = proto_no or (sid.group(1) if sid else "STUDY")
    S["label"] = f"{drug} {proto_no}" if drug else proto_no
    S["description"] = oneline(title)[:300] if title else None
    phase = re.search(r"Phase of Development:.*\n.*?(Phase\s*[0-4I/V-]+(?:/[0-4IV]+)?)\s*$", syn, re.M) or \
        re.search(r"\b(Phase\s*[0-4]+(?:/[0-4]+)?)\b", title or "")
    S["phase"] = phase.group(1) if phase else None
    S["study_type"] = "Interventional Study"
    S["document_name"] = f"{proto_no} Clinical Study Protocol" if proto_no else "Clinical Study Protocol"
    S["document_version"] = S.get("version")
    S["document_status"] = "Final"
    S["document_template"] = "Sponsor"
    S["language"] = "en"
    hist = re.findall(r"(?m)^(Version \d+(?:\.\d+)? \[.+\])\s*$", p1)
    if hist:
        log.append("protocol history (amendments not modelled in this version): " + "; ".join(hist))


def _design(doc, syn, tables, data, log):
    S = data["Study"]
    overview = oneline(doc.block(r"^Study Design Overview:?", [r"^Inclusion/Exclusion Criteria", r"^Investigational Product"], syn) or "")
    low = overview.lower() + " " + (S.get("description") or "").lower()
    if "observer-blind" in low or "observer blind" in low:
        S["blinding"] = "Double Blind Study"
        log.append("REVIEW blinding: protocol says 'observer-blinded' (participant + evaluators blinded); mapped to Double Blind Study")
    elif "double-blind" in low or "double blind" in low:
        S["blinding"] = "Double Blind Study"
    elif "single-blind" in low:
        S["blinding"] = "Single Blind Study"
    elif "open-label" in low or "open label" in low:
        S["blinding"] = "Open Label Study"
    S["model"] = "Group Sequential Design" if ("sequential" in low or "dose-escalation" in low or "ascending dose" in low) else "Parallel Study"
    S["characteristics"] = "Randomized Controlled Clinical Trial" if "random" in low else None
    subs = [k for k, w in (("Safety Study", "safety"), ("Tolerability Study", "tolerab"), ("Immunogenicity Study", "immunogenicity"),
                           ("Dose Response Study", "dose-finding"), ("Pharmacokinetic Study", "pharmacokinetic")) if w in low]
    S["sub_types"] = "; ".join(subs) or None
    S["therapeutic_areas"] = "Vaccines" if "vaccine" in low else None
    S["design_name"] = f"{S['name']} Design"
    S["design_description"] = overview[:1500] or None
    S["design_rationale"] = oneline(doc.block(r"^Dose Rationale\s*$", [r"^Endpoints\s*$", r"^3\s+STUDY POPULATION"]) or "")[:1500] or "See protocol"

    # ---- interventions (Investigational Product / Comparative sections of the synopsis)
    ip = doc.block(r"^Investigational Product\(s\):?", [r"^Concomitant Medication", r"^Duration of Study"], syn) or ""
    prods, intvs, adms = [], [], []
    vaccine = "vaccine" in ip.lower()
    blocks = re.split(r"(?m)^(Study Vaccine:|Study Drug:|Comparative Vaccine\(s\):|Comparator\(s\):|[A-Z][\w ]+ \(([A-Z0-9]+)\):)\s*$", ip)
    # simple parse: product name lines followed by Dose(s): and Mode of Administration:
    entries = []
    m = re.search(r"Study (?:Vaccine|Drug):\s*\n(\S+)", ip)
    if m:
        entries.append((m.group(1), m.group(1), "investigational", ip[m.end():]))
    for m in re.finditer(r"(?m)^(.+?) \(([A-Z0-9]{3,})\):\s*$", ip):
        entries.append((m.group(2), m.group(1).strip(), "comparator", ip[m.end():]))
    for code, long_name, role, rest in entries:
        dose = re.search(r"Dose\(s\):\s*\n(.+?)(?:\n[A-Z][^\n]*:\s*\n|\Z)", rest, re.S)
        route = re.search(r"Mode of Administration:\s*\n(.+?)(?:\n|$)", rest)
        dose_t = oneline(dose.group(1)) if dose else ""
        route_t = oneline(route.group(1)) if route else ""
        low_d = (dose_t + rest[:400]).lower()
        form = ("Injectable Suspension Dosage Form" if "suspension" in low_d
                else "Injectable Solution Dosage Form" if "solution" in low_d else None)
        prods.append({"name": f"{code} product", "label": long_name, "description": dose_t[:300],
                      "dose_form": form,
                      "designation": "Investigational Medicinal Product", "sourcing": "Centrally Sourced"})
        if not form:
            log.append(f"REVIEW dose form for {code}: not found in synopsis - fill Products.dose_form")
        intvs.append({"name": code, "label": long_name, "description": dose_t[:300],
                      "role": "Protocol Agent" if role == "investigational" else "Active Comparator",
                      "type": "Vaccine" if vaccine else "Pharmacologic Substance"})
        levels = re.search(r"at either ([\d, ]+(?:or|and) \d+)\s*(µg|μg|mg|g)", dose_t)
        rte = "Intramuscular Route of Administration" if "intramuscular" in route_t.lower() else None
        vol = re.search(r"(\d+(?:\.\d+)?)\s*mL", dose_t)
        if levels:
            unit = "Microgram" if levels.group(2) in ("µg", "μg") else "Milligram"
            for lv in re.findall(r"\d+(?:\.\d+)?", levels.group(1)):
                adms.append({"intervention": code, "name": f"{code} {lv} {levels.group(2)}", "label": f"{code} {lv} {levels.group(2)}",
                             "description": dose_t[:300], "dose_value": lv, "dose_unit": unit, "route": rte,
                             "frequency": "Once", "product": f"{code} product", "duration_text": "Single dose"})
        else:
            adms.append({"intervention": code, "name": f"{code} {vol.group(1) if vol else ''} mL".strip(),
                         "label": f"{code} {vol.group(1) if vol else ''} mL".strip(), "description": dose_t[:300],
                         "dose_value": vol.group(1) if vol else None, "dose_unit": "Milliliter" if vol else None, "route": rte,
                         "frequency": "Once", "product": f"{code} product", "duration_text": "Single dose"})
    data["Products"], data["Interventions"], data["Administrations"] = prods, intvs, adms

    # ---- epochs from SoA headers, arms from interventions, cohorts from 'Group n' tables
    epochs = OrderedDict()
    for t in tables:
        for c in t["columns"]:
            if c["epoch"]:
                epochs.setdefault(c["epoch"], None)
    def etype(e):
        e = e.lower()
        if "screen" in e or "run-in" in e or "baseline" in e:
            return "Screening Epoch"
        if re.search(r"follow|post|end of study|\beos\b|safety", e):
            return "Follow-Up Epoch"
        return "Treatment Epoch"
    data["Epochs"] = [{"name": e, "label": e, "description": f"{e} epoch", "type": etype(e)} for e in epochs]
    groups = []
    for t in (tables if (len(tables) > 1 or any(re.search(r"\(Group \d+\)", t["title"]) for t in tables)) else []):
        g = re.search(r"\(Group (\d+)\)", t["title"])
        ages = re.search(r"(\d+) to (\d+) Years", t["title"])
        groups.append({"name": f"GROUP{g.group(1)}" if g else f"TL{len(groups) + 1}",
                       "label": t["title"].split(":", 1)[-1].strip() if ":" in t["title"] else t["title"],
                       "min_age": ages.group(1) if ages else None, "max_age": ages.group(2) if ages else None})
    for g in groups:
        n = None
        if g["name"].startswith("GROUP"):
            k = g["name"][-1]
            n = re.search(rf"\(n\s*=\s*(\d+)\)\s*\(Group {k}\)", syn) or re.search(rf"Group {k}\)[^.]*?\(n\s*=\s*(\d+)\)", syn)
        g.update({"description": g["label"], "healthy": "Y", "age_unit": "Year", "sex": "Female; Male",
                  "planned_enrollment": n.group(1) if n else None})
    m1 = re.search(r"Stage 1: A total of approximately (\d+)", syn)
    m2 = re.search(r"Approximately (\d+) subjects will be enrolled in Stage 2", syn)
    for g in groups:
        if g["name"] == "GROUP1" and m1 and not g["planned_enrollment"]:
            g["planned_enrollment"] = m1.group(1)
        if g["name"] == "GROUP2" and m2 and not g["planned_enrollment"]:
            g["planned_enrollment"] = m2.group(1)
    data["Cohorts"] = groups
    tot = [int(g["planned_enrollment"]) for g in groups if g.get("planned_enrollment")]
    if not groups:
        tot = []
    ages = [int(a) for g in groups for a in (g.get("min_age"), g.get("max_age")) if a]
    data["Population"] = {"name": "POP", "label": "Study population", "description": oneline(doc.block(r"^Study Population:", [r"^Number of Subjects"], syn) or "") or None,
                          "healthy": "Y", "min_age": min(ages) if ages else None, "max_age": max(ages) if ages else None,
                          "age_unit": "Year", "planned_enrollment": sum(tot) if tot else None, "sex": "Female; Male"}
    if groups and len(tot) != len(groups):
        log.append("REVIEW planned enrolment: not found for every group")

    arms = []
    for iv in intvs:
        cohorts = [g["name"] for g in groups if iv["role"] != "Active Comparator" or True]
        arms.append({"name": iv["name"], "label": iv["label"], "description": f"{iv['label']} arm",
                     "type": "Investigational Arm" if iv["role"] == "Protocol Agent" else "Active Comparator Arm",
                     "data_origin_type": "Data Generated Within Study", "data_origin_description": "Data collected in study"})
    # cohort membership from group tables: a group receives an intervention if its SoA title / synopsis says so
    for a in arms:
        mem = []
        for g in groups:
            gtxt = re.search(rf"Group {g['name'][-1]} - (.+?)(?:;|\.)", overview)
            recv = re.search(r"(?:\bto|receive)\s+(.+)$", gtxt.group(1)) if gtxt else None
            if recv and re.search(rf"\b{re.escape(a['name'])}\b", recv.group(1)):
                mem.append(g["name"])
        a["cohorts"] = "; ".join(mem) or None
        if not mem:
            log.append(f"REVIEW arm {a['name']}: could not determine which groups receive it")
    data["Arms"] = arms
    els = [{"name": "EL_" + e.upper().replace("-", "_"), "label": e, "description": f"{e} period"}
           for e in epochs if e.lower() != "dosing"]
    for iv in intvs:
        els.append({"name": f"EL_{iv['name']}", "label": f"{iv['name']} administration", "description": f"Single dose of {iv['label']}",
                    "interventions": iv["name"]})
    data["Elements"] = els
    cells = []
    for a in arms:
        for e in epochs:
            el = f"EL_{a['name']}" if e.lower() == "dosing" else "EL_" + e.upper().replace("-", "_")
            cells.append({"arm": a["name"], "epoch": e, "elements": el})
    data["Cells"] = cells
    data["AnalysisPopulations"] = []
    for m in re.finditer(r"(?m)^(Safety|Immunogenicity|Per Protocol|Full Analysis) (?:Analysis )?Population\s*[:(]\s*(.+?)(?:\n|$)", syn):
        data["AnalysisPopulations"].append({"name": m.group(1).upper().replace(" ", "_"), "label": m.group(1) + " Population",
                                            "text": oneline(m.group(2))})
    if not data["AnalysisPopulations"]:
        m = re.search(r"Safety analyses will be performed on the safety population;\s*(.+?)\.", oneline(syn))
        if m:
            data["AnalysisPopulations"].append({"name": "SAF", "label": "Safety Population", "text": oneline(m.group(1))})


# -------------------------------------------------------------------------------- objectives / endpoints
def _stage_split(text):
    parts = re.split(r"(?m)^(Stage \d(?: ?[;,]? Group \d)?):\s*$", text)
    out = []
    if parts[0].strip():
        out.append(("", parts[0]))
    for i in range(1, len(parts), 2):
        out.append((parts[i], parts[i + 1]))
    return out


def _bullets(text):
    """bulleted endpoint list -> [(context heading, item text)].  Non-bulleted sentences become items too;
    lines ending with ':' become the context for the bullets that follow."""
    items, ctx, cur, para = [], "", None, []

    def flush():
        nonlocal para, ctx
        t = oneline(" ".join(para))
        if t:
            if t.endswith(":"):
                ctx = t
            else:
                items.append([ctx, t])
        para = []
    lines = [l for l in text.split("\n") if l.strip()]
    is_bul = [bool(re.match(r"^\s*[•∀]", l)) for l in lines]
    heading = [False] * len(lines)
    for i, l in enumerate(lines):
        if not is_bul[i] and l.strip()[:1].isupper():
            if l.rstrip().endswith(":") or (i + 1 < len(lines) and not is_bul[i + 1] and lines[i + 1].rstrip().endswith(":")):
                heading[i] = True
    for i, line in enumerate(lines):
        m = re.match(r"^\s*[•∀]\s*(.*)$", line)
        if m:
            flush()
            cur = [ctx, m.group(1)]
            items.append(cur)
            continue
        if cur is not None and not heading[i] and not (line.strip()[:1].isupper() and cur[1].rstrip().endswith((".", ":"))):
            cur[1] += " " + line.strip()
            continue
        cur = None
        para.append(line.strip())
        if line.rstrip().endswith(":"):
            flush()
    flush()
    return [(c, oneline(t)) for c, t in items if oneline(t)]


def _objectives_endpoints(syn, data, log):
    ob = syn and re.search(r"(?ms)^Study Objective\(s\):\s*$(.+?)^Planned Total Number", syn)
    objs, eps = [], []
    if ob:
        for stage, body in _stage_split(ob.group(1)):
            st = re.sub(r"\D", "", stage) or "0"
            for lvl, txt in re.findall(r"(?ms)^(Primary|Secondary|Exploratory) Objectives?:\s*(.+?)(?=^(?:Primary|Secondary|Exploratory) Objectives?:|\Z)", body):
                sents = [s.strip() for s in re.split(r"(?m)^(?=To )", txt) if s.strip()]
                for i, s in enumerate(sents, 1):
                    objs.append({"name": f"OBJ_S{st}_{lvl[:3].upper()}{i}", "label": f"{stage} {lvl.lower()} objective {i}".strip(),
                                 "level": lvl, "text": oneline(s)})
    ep = syn and re.search(r"(?ms)^Endpoints for Evaluation:\s*$(.+?)^Statistical Methods", syn)
    if ep:
        for stage, body in _stage_split(ep.group(1)):
            st = re.search(r"Stage (\d)", stage)
            st = st.group(1) if st else "0"
            grp = re.search(r"Group (\d)", stage)
            sections = re.split(r"(?m)^(Primary|Secondary|Exploratory) Endpoints\s*$", body)
            chunks = [("Secondary" if grp and grp.group(1) == "3" else "Primary", sections[0])] if sections[0].strip() else []
            chunks += [(sections[i], sections[i + 1]) for i in range(1, len(sections), 2)]
            for lvl, chunk in chunks:
                for ctx, b in _bullets(chunk):
                    cand = [o for o in objs if o["name"].startswith(f"OBJ_S{st}_{lvl[:3].upper()}")]
                    if grp and grp.group(1) == "3":
                        cand = [o for o in cand if "PPSV23" in o["text"]] or cand
                    elif cand and len(cand) > 1:
                        cand = [o for o in cand if "PPSV23" not in o["text"]] or cand
                    target = cand[0]["name"] if cand else None
                    eps.append({"objective": target, "name": f"EP_{stage.replace(' ', '').replace(';', '')}_{lvl[:3].upper()}{len(eps) + 1}",
                                "level": lvl, "purpose": "Safety" if lvl == "Primary" else "Immunogenicity", "text": b,
                                "label": b[:60], "description": ctx or None})
                    if not target:
                        log.append(f"REVIEW endpoint '{b[:50]}...' could not be linked to an objective")
    data["Objectives"] = objs
    data["Endpoints"] = [e for e in eps if e["objective"]]
    log.append(f"objectives: {len(objs)}, endpoints: {len(data['Endpoints'])}")


# -------------------------------------------------------------------------------- eligibility
def _numbered(text):
    items, cur = [], None
    for line in text.split("\n"):
        m = re.match(r"^\s*(\d{1,2})\.\s*([A-Z(].*)$", line)
        if m and (cur is None or int(m.group(1)) == cur[0] + 1):
            cur = [int(m.group(1)), m.group(2)]
            items.append(cur)
        elif cur is not None:
            cur[1] += " " + line.strip()
    return [(n, oneline(t)) for n, t in items]


def _eligibility(syn, data, log):
    inc = re.search(r"(?ms)^Inclusion:\s*$(.+?)^Waivers to the inclusion", syn or "")
    exc = re.search(r"(?ms)^Exclusion:\s*$(.+?)^Waivers to the exclusion", syn or "")
    rows = []
    for cat, m in (("Inclusion Criteria", inc), ("Exclusion Criteria", exc)):
        if not m:
            log.append(f"REVIEW {cat}: block not found")
            continue
        body = re.sub(r"(?m)^A subject (?:is eligible|will be excluded).*?:\s*$", "", m.group(1))
        for n, t in _numbered(body):
            p = "INC" if cat.startswith("I") else "EXC"
            rows.append({"identifier": f"{p[0]}{n}", "category": cat, "name": f"{p}{n:02d}",
                         "label": f"{cat.split()[0]} {n}", "text": t})
    data["Eligibility"] = rows
    log.append(f"eligibility criteria: {sum(r['category'].startswith('I') for r in rows)} inclusion, "
               f"{sum(r['category'].startswith('E') for r in rows)} exclusion")


# -------------------------------------------------------------------------------- abbreviations / narrative
def _abbreviations(doc, data, log):
    rows = []
    start = next((i for i, t in enumerate(doc.pages) if re.search(r"(?m)^Abbreviations?\s+(?:Description of abbreviations|Expanded Term|Definition|Term|Meaning|Description|Full Term)", t)), None)
    if start is None:
        data["Abbreviations"] = []
        log.append("abbreviations: list not found")
        return
    for i in range(start, min(start + 6, len(doc.pages))):
        t = doc.pages[i]
        ws = doc.words(i)
        lines = {}
        for w in ws:
            lines.setdefault(round(w["top"]), []).append(w)
        xs = sorted(w["x0"] for w in ws if w["text"] in ("Description", "Expanded", "Definition", "Term", "Meaning", "Full"))
        split_x = xs[0] - 2 if xs else 150
        for top in sorted(lines):
            lw = sorted(lines[top], key=lambda w: w["x0"])
            full = " ".join(w["text"] for w in lw)
            if doc._key(full) in doc.boiler or re.match(r"^\d{2} \w{3} \d{4}\b", full) or full.startswith(("Sponsor:", "- CONFIDENTIAL")):
                continue
            left = " ".join(w["text"] for w in lw if w["x0"] < split_x)
            right = " ".join(w["text"] for w in lw if w["x0"] >= split_x)
            if left in ("Abbreviations", "Abbreviation"):
                continue
            if left in ("Terms",) or "Definition of" in left + right:
                break
            if left and right:
                rows.append({"abbreviation": left, "expansion": right})
            elif left:
                if rows and not rows[-1]["abbreviation"]:
                    rows[-1]["abbreviation"] = left
                else:
                    rows.append({"abbreviation": left, "expansion": ""})
            elif right:
                if rows and (right[0].islower() or right in ("®", "™") or not rows[-1]["expansion"]):
                    rows[-1]["expansion"] = (rows[-1]["expansion"] + ("" if right in ("®", "™") else " ") + right).strip()
                else:
                    rows.append({"abbreviation": None, "expansion": right})
        if "Definition of Key Study Terms" in t:
            break
    data["Abbreviations"] = [r for r in rows if r["abbreviation"] and r["expansion"] and len(r["abbreviation"]) <= 20
                             and not re.search(r"Appendix|^\d+(\.\d+)+", r["abbreviation"])]
    log.append(f"abbreviations: {len(data['Abbreviations'])}")


def _narrative(doc, data, log):
    heads = [(m.start(), m.end(), m.group(1), m.group(2)) for m in
             re.finditer(r"(?m)^(\d{1,2})\s+([A-Z][A-Z ,()/'&-]{3,})$", doc.text)]
    rows = []
    for i, (s, e, num_, title) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(doc.text)
        body = doc.text[e:end].strip()
        if len(body) > 32000:
            body = body[:32000] + "\n[truncated - Excel cell limit]"
        rows.append({"section_number": num_, "section_title": title.title(), "text": body})
    data["Narrative"] = rows
    log.append(f"narrative sections: {len(rows)} (top-level only; sub-section numbers are not in the PDF text layer)")


# -------------------------------------------------------------------------------- Schedule of Assessments
def _clusters(vals, tol=3):
    out = []
    for v in sorted(vals):
        if out and v - out[-1] <= tol:
            continue
        out.append(v)
    return out


def _soa_tables(doc, log):
    tables = []
    for i, t in enumerate(doc.pages):
        if not re.search(r"Schedule of Assessments", t) or not re.search(r"Visit (?:Number|Day)", t):
            continue
        page = doc.pdf.pages[i]
        ws = doc.words(i)
        title_w = [w for w in ws if w["text"].startswith("Table")]
        title_line = next((l for l in t.split("\n") if "Schedule of Assessments" in l), "")
        if not re.match(r"^\s*Table\s*\d+", title_line):
            continue
        vr = _clusters([r["x0"] for r in page.rects if r["height"] > 8 and r["width"] < 2])
        segw = Counter()
        for r in page.rects:
            if r["height"] < 2 and r["width"] > 2:
                segw[round(r["top"])] += r["width"]
        hz = _clusters([y for y, wsum in segw.items() if wsum > page.width * 0.5])
        if len(vr) < 3 or len(hz) < 4:
            log.append(f"REVIEW page {i + 1}: SoA table without ruling lines - not parsed")
            continue
        label_right = vr[1]
        bounds = list(zip(vr[1:-1], vr[2:]))
        bands = list(zip(hz[:-1], hz[1:]))

        def in_band(w, b):
            mid = (w["top"] + w["bottom"]) / 2
            return b[0] <= mid < b[1]

        def col_of(x):
            for ci, (a, b) in enumerate(bounds):
                if a <= x < b:
                    return ci
            return None
        # header bands: epoch row / visit number row / visit day row
        hdr = {}
        for b in bands[:4]:
            bw = [w for w in ws if in_band(w, b)]
            lab = " ".join(w["text"] for w in sorted(bw, key=lambda w: w["x0"]) if w["x0"] < label_right)
            if "Visit Number" in lab:
                hdr["number"] = b
            elif re.search(r"Visit Days?", lab):
                hdr["day"] = b
            elif lab.startswith("Assessments"):
                hdr["epoch"] = b
        ncol = len(bounds)
        cols = [{"epoch": None, "number": None, "day_text": None, "notes": ""} for _ in range(ncol)]
        for key in ("number", "day"):
            if key in hdr:
                for w in ws:
                    if in_band(w, hdr[key]) and w["x0"] >= label_right:
                        ci = col_of((w["x0"] + w["x1"]) / 2)
                        if ci is not None:
                            k = "number" if key == "number" else "day_text"
                            cols[ci][k] = ((cols[ci][k] or "") + " " + w["text"]).strip()
        if "epoch" in hdr:
            ew = [w for w in ws if in_band(w, hdr["epoch"]) and w["x0"] >= label_right and w["size"] > 7]
            for w in ew:
                c = (w["x0"] + w["x1"]) / 2
                k = col_of(c)
                if k is None:
                    continue
                best = (k, k)
                for a in range(k, -1, -1):
                    if a != k and cols[a]["epoch"]:
                        break
                    for b in range(k, ncol):
                        if b != k and cols[b]["epoch"]:
                            break
                        mid = (bounds[a][0] + bounds[b][1]) / 2
                        if abs(mid - c) < 12 and (b - a) > (best[1] - best[0]):
                            best = (a, b)
                for ci in range(best[0], best[1] + 1):
                    cols[ci]["epoch"] = w["text"].strip()
        # columns -> day / window / notes
        if "epoch" in hdr:
            for w in ws:
                if in_band(w, hdr["epoch"]) and w["size"] < 8 and re.fullmatch(r"[a-z]", w["text"]):
                    ci = col_of(w["x0"] - 4)
                    if ci is not None:
                        cols[ci]["sup"] = cols[ci].get("sup", "") + w["text"]
        for c in cols:
            dt = c["day_text"] or ""
            c["notes"] = "".join(ch for ch in dt if ch in MARK_SYMBOLS) + c.get("sup", "")
            dt = "".join(ch for ch in dt if ch not in MARK_SYMBOLS)
            m = re.match(r"^\s*(-?\d+)\s*to\s*(-?\d+)", dt)
            m2 = re.match(r"^\s*(-?\d+)\s*(?:\((?:±|\+/-)\s*(\d+)\)|\(\+(\d+)\)|\(-(\d+)\))?", dt)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                c.update(day=a, window_lower=0, window_upper=b - a)
            elif m2 and m2.group(1):
                d = int(m2.group(1))
                pm, plus, minus = m2.group(2), m2.group(3), m2.group(4)
                c.update(day=d, window_lower=int(pm or minus or 0) if (pm or minus) else None,
                         window_upper=int(pm or plus or 0) if (pm or plus) else None)
            else:
                c.update(day=None, window_lower=None, window_upper=None)
        # activity rows
        start_y = max(b[1] for b in hdr.values()) if hdr else bands[0][1]
        rows = []
        bars = [r for r in page.rects if r["height"] <= 1.5 and r["width"] > 20 and r["x0"] >= label_right - 40
                and r.get("non_stroking_color") not in (None, 0, 1, [0], [1], (0,), (1,), [0, 0, 0], (0, 0, 0), [1, 1, 1], (1, 1, 1))
                and r["width"] < page.width * 0.6]
        sub_bands = []
        for b in bands:
            if b[0] < start_y:
                continue
            cuts = sorted(w["top"] for w in ws if w["text"] == "CCI" and in_band(w, b))
            if not cuts:
                sub_bands.append(b)
                continue
            edges = [b[0]]
            for c in cuts:
                edges += [c - 1, c + 12]
            edges.append(b[1])
            for a, z in zip(edges[::2], edges[1::2]):
                if z - a > 4:
                    sub_bands.append((a, z))
            sub_bands.append((cuts[0] - 1, cuts[0] + 12, "CCI"))
        for b in sub_bands:
            if len(b) == 3:
                continue
            bw = sorted([w for w in ws if in_band(w, b)], key=lambda w: (round(w["top"]), w["x0"]))
            label_w = [w for w in bw if w["x0"] < label_right]
            if not label_w:
                continue
            fn = [w["text"] for w in label_w if w["size"] < 8 and re.fullmatch(r"[a-z]{1,2}", w["text"])]
            label = " ".join(w["text"] for w in label_w if not (w["size"] < 8 and re.fullmatch(r"[a-z]{1,2}", w["text"])))
            label = re.sub(r"\s+", " ", label).strip()
            if label.startswith(("AE:", "PIMMC:", "Footnotes", "†", "‡")) or label in ("CCI",):
                continue
            label = re.sub(r"\bCCI\b", "", label).strip()
            redacted = False
            hasx, marks = [False] * ncol, [[] for _ in range(ncol)]
            for w in bw:
                if w["x0"] < label_right:
                    continue
                ci = col_of((w["x0"] + w["x1"]) / 2)
                if ci is None:
                    continue
                if w["text"].upper().startswith("X"):
                    hasx[ci] = True
                    for k, ch in enumerate(w["text"][1:]):
                        marks[ci].append((w["x0"] + 1 + k, ch))
                elif w["text"] in MARK_SYMBOLS or (w["size"] < 8 and re.fullmatch(r"[a-z]{1,2}", w["text"])):
                    near = col_of(w["x0"] - 6)
                    marks[near if near is not None else ci].append((w["x0"], w["text"]))
            cells = [("X" + "".join(t for _, t in sorted(marks[i]))) if hasx[i] else "" for i in range(ncol)]
            cont = False
            for r in bars:
                mid = (r["top"] + r["bottom"]) / 2
                if b[0] <= mid < b[1]:
                    for ci, (a, z) in enumerate(bounds):
                        if r["x0"] <= (a + z) / 2 <= r["x1"]:
                            cells[ci] = cells[ci] or "X"
                            cont = True
            if not any(cells) and not label:
                continue
            rows.append({"label": label, "footnotes": fn, "cells": cells, "continuous": cont, "redacted": redacted})
        # footnotes from this page and following pages until the next table / section
        fn_text = []
        for j in range(i, min(i + 4, len(doc.pages))):
            pt = doc.pages[j]
            if j > i and (re.search(r"Schedule of Assessments", pt) and re.search(r"Visit (?:Number|Day)", pt)):
                break
            fn_text.append(pt)
            if j > i and re.search(r"(?m)^1\s+INTRODUCTION\s*$", pt):
                break
        notes = _footnotes("\n".join(fn_text))
        n_cci = sum(1 for b in sub_bands if len(b) == 3)
        tables.append({"page": i + 1, "title": oneline(title_line), "columns": cols, "rows": rows, "footnotes": notes})
        log.append(f"SoA '{oneline(title_line)[:70]}' (p{i + 1}): {ncol} visits, {len(rows)} rows, {len(notes)} footnotes, "
                   f"{n_cci} redacted (CCI) row(s) skipped")
        if n_cci:
            log.append(f"REVIEW p{i + 1}: {n_cci} SoA row(s) redacted as 'CCI' in this anonymised PDF - content unknown")
    return tables


def _footnotes(text):
    notes, cur = OrderedDict(), None
    for line in text.split("\n"):
        m = re.match(r"^\s*([†‡§*¶])\s+(.*)$", line) or re.match(r"^\s*([a-z]{1,2})\.\s+(.*)$", line)
        if m:
            cur = m.group(1)
            notes[cur] = m.group(2).strip()
        elif cur and line.strip() and not re.match(r"^(Table|Flow Chart|Sponsor:|\d+\s+[A-Z]{4,})", line.strip()):
            if re.match(r"^[A-Z][A-Za-z ]+:", line.strip()) and len(line) < 40:
                cur = None
                continue
            notes[cur] += " " + line.strip()
    out = {}
    for k, v in notes.items():
        v = re.sub(r"\s*Footnotes (?:continued|appear) on next page.*$", "", oneline(v))
        if v:
            out[k] = v
    return out


# -------------------------------------------------------------------------------- lab appendix
def _lab_panels(doc, log):
    panels: dict[str, list[tuple[str, str | None]]] = OrderedDict()
    hits = [i for i, t in enumerate(doc.pages) if "Parameters to be Analyzed" in t]
    run = hits[:1]
    for h in hits[1:]:
        if h == run[-1] + 1:
            run.append(h)
        else:
            break
    for i in run:
        t = doc.pages[i]
        ws = doc.words(i)
        hdr = [w for w in ws if w["text"] == "Parameters"]
        vis = [w for w in ws if w["text"] == "Visit"]
        if not hdr:
            continue
        px, vx, top0 = hdr[0]["x0"], (vis[0]["x0"] if vis else hdr[0]["x0"] - 100), hdr[0]["bottom"]
        lines = OrderedDict()
        for w in sorted(ws, key=lambda w: (round(w["top"]), w["x0"])):
            if w["top"] <= top0 or w["text"] in ("CCI",):
                continue
            lines.setdefault(round(w["top"] / 2), []).append(w)
        cat, sub = None, None
        for k, lw in lines.items():
            left = " ".join(w["text"] for w in lw if w["x0"] < vx - 2)
            params = [w for w in lw if w["x0"] >= px - 2 and w["text"] not in ("•", "∀", "")]
            if left and not left[0].islower() and not left.startswith(("Sponsor", "02 Dec", "Version")) and \
                    not re.match(r"^\d{2} \w{3}", left):
                if left.startswith(("of ", "only", "Table continued", "Laboratory Test")):
                    pass
                else:
                    cat = re.sub(r"\s*\(.*$", "", left).strip()
                    panels.setdefault(cat, [])
                    sub = None
            if not params or cat is None:
                continue
            txt = " ".join(w["text"] for w in params)
            if re.search(r"\bPage \d+ of\b|^\d+ of \d+$", txt) or doc._key(txt) in doc.boiler:
                continue
            indent = min(w["x0"] for w in params)
            if re.match(r"^(Using dipstick|Microscopy)", txt):
                sub = txt
                continue
            if indent < px + 30:
                sub = None if not sub or indent < px + 10 else sub
            panels[cat].append((txt, sub))
    panels = OrderedDict((k, v) for k, v in panels.items() if v)
    if panels:
        log.append("lab appendix panels: " + ", ".join(f"{k} ({len(v)})" for k, v in panels.items()))
    return panels


# -------------------------------------------------------------------------------- activities + BCs
PANEL_WORDS = {"hematology": "Hematology", "biochemistry": "Biochemistry", "coagulation profile": "Coagulation Profile",
               "hepatic profile": "Hepatic Profile", "urinalysis": "Urinalysis"}
CATEGORY_OF = {"Hematology": "Hematology", "Biochemistry": "Clinical Chemistry", "Hepatic Profile": "Clinical Chemistry",
               "Coagulation Profile": "Coagulation", "Urinalysis": "Urinalysis", "Vital Signs": "Vital Signs", "ECG": "ECG",
               "Virology": "Serology", "Pregnancy Test": "Pregnancy Test", "Urine Drug Screening": "Substance Use"}


def _act_name(label, used=None):
    s = re.sub(r"\(.*?\)", "", label)
    if used is not None:
        cand = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").upper()[:40] or "ACTIVITY"
        if cand in used and norm(used[cand]) != norm(label):
            s = label
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").upper()
    return s[:40] or "ACTIVITY"


def _schedule(tables, panels, lib, data, log):
    acts: OrderedDict = OrderedDict()
    encs: OrderedDict = OrderedDict()
    conds, soas, tl_rows = [], [], []
    bcs, surrogates, cats = OrderedDict(), OrderedDict(), OrderedDict()

    all_labels = [r["label"] for t in tables for r in t["rows"]]
    label_of = {}
    intervention_names = [iv["name"] for iv in data.get("Interventions", []) if len(iv["name"]) > 2 and iv["name"] != "Placebo"]

    def add_bc(m, evidence, act_label):
        e = m.entry
        if e is None:
            return None
        if e.kind == "surrogate":
            surrogates.setdefault(e.name, {"name": e.name, "label": e.name,
                                           "description": f"Protocol: {evidence}. Library definition: {e.label}",
                                           "reference": e.reference, "evidence": evidence, "review_status": "needs review"})
            return ("sur", e.name)
        cat = e.category or CATEGORY_OF.get(act_label)
        if e.name not in bcs:
            bcs[e.name] = {"name": e.name, "label": e.label or e.name, "ncit_code": e.ncit_code, "reference": e.reference,
                           "category": cat, "source": e.source, "method": m.method, "confidence": m.score,
                           "evidence": evidence + (f" | LIBRARY ISSUE: {m.issue}" if m.issue else ""), "review_status": m.review}
            if cat:
                cats.setdefault(cat, {"name": cat, "label": cat, "description": f"{cat} biomedical concepts"})
        return ("bc", e.name)

    def map_activity(label, fn_texts):
        """returns (bc names, surrogate names, method summary, confidence)"""
        out_bc, out_sur, methods, confs = [], [], [], []
        head = re.sub(r"\s*\(.*?\)\s*", " ", label).strip()
        allp = re.findall(r"\((.*?)\)", label)
        spec_ctx = "urine" if any(x.strip().lower() == "urine" for x in allp) else None
        paren = [x for x in allp if x.strip().lower() not in ("urine", "serum", "blood", "plasma", "paper", "optional")
                 and not re.fullmatch(r"[A-Z0-9-]{2,8}", x.strip())]
        items = []
        exp = lib.expand(label) or lib.expand(head)
        panel_hits = [p for k, p in PANEL_WORDS.items() if k in label.lower() and p in panels]
        if not panel_hits and panels and re.search(r"\blab(?:oratory)?\b", label, re.I):
            # generic 'Safety Laboratory Test' row: every panel not covered by its own SoA row
            panel_hits = [p for p in panels if p != "Other" and not any(
                norm(p)[:6] in norm(o) for o in all_labels if norm(o) != norm(label))]
        lab_cat = next((c for c in panels if norm(c) == norm(head) or norm(c).startswith(norm(head))
                        or norm(head).startswith(norm(c))), None)
        expo = re.search(r"infusion|\bdosing\b|dose administration|injection|immuni[sz]ation|vaccination|study drug|"
                         r"\bIMP\b|vaccine administration", label, re.I) and not re.search(r"sample|blood for|pk\b", label, re.I)
        if panel_hits:
            for p in panel_hits:
                for prm, sub in panels.get(p, []):
                    term = re.sub(r"\s*\((?!IgM).*?\)\s*$", "", prm).strip()
                    spec = "urine" if "urin" in p.lower() else None
                    if sub and sub.startswith("Microscopy"):
                        items.append(("__microscopy__", prm, p, spec))
                    else:
                        items.append((term, prm, p, spec))
        elif expo and not exp:
            items = [("study drug administration", label, head, None)]
        elif paren and not lib.expand(label):
            for part in re.split(r",|\band\b", paren[0]):
                if part.strip():
                    items.append((part.strip(), part.strip(), head, spec_ctx))
        elif exp:
            items = [(x, label, head, spec_ctx) for x in exp]
        elif lab_cat and panels.get(lab_cat):
            whole = lib.match(head, specimen=spec_ctx)
            spec = "urine" if "urin" in lab_cat.lower() else spec_ctx
            if whole.entry is not None and whole.method.startswith(("exact", "surrogate (library)")):
                items = [(head, label, head, spec_ctx)]
            else:
                items = [(re.sub(r"\s*\((?!Ig[MGA]\)).*?\)\s*$", "", prm).strip(), prm, lab_cat, spec) for prm, _ in panels[lab_cat]]
        else:
            whole = lib.match(head, specimen=spec_ctx)
            parts = [x for x in re.split(r"\s*(?:&|\band\b|,)\s*", head) if len(x) > 2]
            if (whole.entry is None or whole.score < 0.9) and len(parts) > 1:
                pm = [lib.match(x, specimen=spec_ctx) for x in parts]
                if sum(1 for m_ in pm if m_.entry is not None and m_.score >= 0.9) >= 1:
                    items = [(x, label, head, spec_ctx) for x in parts]
            if not items:
                items = [(head, label, head, spec_ctx)]
        micro = [i for i in items if i[0] == "__microscopy__"]
        items = [i for i in items if i[0] != "__microscopy__"]
        for term, src, grp, spec in items:
            exp2 = lib.expand(term)
            terms = exp2 if exp2 else [term]
            for tt in terms:
                m = lib.match(tt, specimen=spec)
                ref = add_bc(m, f"'{src}' ({grp})" + (" - expanded from curated panel" if exp2 or exp else ""), head)
                if ref is None:
                    base = head if len(head) >= 8 else label
                    sname = f"{base}: {term}" if term not in (head, label) else base
                    surrogates.setdefault(sname, {"name": sname, "label": sname, "reference": "Sponsor-defined",
                                                  "description": f"No CDISC BC / NCIt match for '{src}'",
                                                  "evidence": f"alternatives considered: {', '.join(m.alternatives) or 'none'}",
                                                  "review_status": "needs review"})
                    out_sur.append(sname)
                    methods.append("surrogate")
                    confs.append(0.0)
                elif ref[0] == "bc":
                    out_bc.append(ref[1]); methods.append(m.method); confs.append(m.score)  # noqa: E702
                else:
                    out_sur.append(ref[1]); methods.append(m.method); confs.append(m.score)  # noqa: E702
        if micro:
            sname = "Urine Microscopy"
            surrogates.setdefault(sname, {"name": sname, "label": sname, "reference": "Sponsor-defined",
                                          "description": "Optional urine microscopy: " + ", ".join(i[1] for i in micro),
                                          "evidence": "lab appendix", "review_status": "needs review"})
            out_sur.append(sname)
        # footnote term-spotting: parameters mentioned in the activity's footnote but not in its label
        spot = ["respiratory rate"] + [k for k, v in lib.evs_cache.items() if v and v.get("name", "").endswith("Measurement")]
        covered = " ".join(norm(i[1]) for i in items) + " " + norm(label)
        for ft in fn_texts:
            for key in spot:
                if key in norm(ft) and key not in covered:
                    m = lib.match(key)
                    ref = add_bc(m, f"footnote: '{ft[:120]}...'", head)
                    if ref and ref[0] == "bc" and ref[1] not in out_bc:
                        out_bc.append(ref[1]); methods.append("footnote"); confs.append(0.7)  # noqa: E702
                        bcs[ref[1]]["method"], bcs[ref[1]]["confidence"], bcs[ref[1]]["review_status"] = \
                            f"footnote term-spotting + {m.method}", 0.6 if m.method == "evs" else 0.7, "needs review"
        return list(OrderedDict.fromkeys(out_bc)), list(OrderedDict.fromkeys(out_sur)), methods, confs

    for ti, t in enumerate(tables, 1):
        g = re.search(r"\(Group (\d+)\)", t["title"])
        tname = f"GROUP{g.group(1)}" if g else ("MAIN" if len(tables) == 1 else f"TIMELINE{ti}")
        sheet = f"SoA_{ti}"
        tl_rows.append({"name": tname, "label": t["title"].split(":", 1)[-1].strip(), "description": t["title"],
                        "main": "Y" if ti == 1 else "N", "entry_condition": "Informed consent signed", "sheet": sheet})
        cols = []
        for c in t["columns"]:
            d = c.get("day")
            dtxt = re.sub(r"\s+", " ", c.get("day_text") or "")
            if d is not None:
                ename = "SCREENING" if d < 1 else f"DAY{d}"
                elabel = "Screening" if d < 1 else f"Day {d}"
            else:
                ename = (re.sub(r"\W+", "_", dtxt.upper()).strip("_")[:40] or f"VISIT{c.get('number') or ''}") or f"COL{len(cols) + 1}"
                elabel = dtxt[:60] or f"Visit {c.get('number')}"
            phone = "phone" in dtxt.lower() or any("phone" in t["footnotes"].get(mk, "").lower() for mk in c["notes"])
            encs.setdefault(ename, {"name": ename, "label": elabel,
                                    "description": ("Screening visit" if ename == "SCREENING" else f"Study day {d} visit" if d is not None
                                                    else f"Unscheduled / conditional visit: {dtxt}"), "type": "Visit",
                                    "setting": None if phone else "Clinic",
                                    "contact_mode": "Telephone Call; In Person" if phone else "In Person"})
            cols.append({"epoch": c["epoch"], "encounter": ename, "day": d, "window_lower": c.get("window_lower"),
                         "window_upper": c.get("window_upper"),
                         "label": f"Visit {c['number']}" if c.get("number") else (dtxt[:30] or None), "notes": c["notes"] or None})
        rows = []
        for r in t["rows"]:
            if not r["label"]:
                continue
            aname = _act_name(r["label"], label_of)
            label_of.setdefault(aname, r["label"])
            fn_texts = [t["footnotes"].get(f, "") for f in r["footnotes"]]
            if aname not in acts:
                bcl, surl, methods, confs = map_activity(r["label"], fn_texts)
                cat = next((CATEGORY_OF[k] for k in CATEGORY_OF if k.lower() in r["label"].lower()), None)
                if "laboratory" in r["label"].lower():
                    cat = None
                desc = []
                if r.get("group"):
                    desc.append(f"SoA group: {r['group']}.")
                if r["continuous"]:
                    desc.append("Collected continuously across the visits shown (shaded bar in SoA).")
                if any("{" in (c or "") for c in r["cells"]):
                    desc.append("Frequency within visit shown in braces in the SoA cells, e.g. X{BID}.")
                acts[aname] = {"name": aname, "label": re.sub(r"\s+", " ", r["label"]), "description": " ".join(desc) or None,
                               "bc_category": cat if cat in cats else None,
                               "biomedical_concepts": "; ".join(bcl) or None, "surrogates": "; ".join(surl) or None,
                               "source_text": f"{t['title'][:60]} p{t['page']}: '{r['label']}'",
                               "method": ", ".join(sorted(set(methods))) or None,
                               "confidence": round(min(confs), 2) if confs else None,
                               "review_status": "auto-accepted" if confs and min(confs) >= 0.9 and "surrogate" not in methods else "needs review"}
            rows.append({"activity": aname, "cells": [c or None for c in r["cells"]]})
            for f in r["footnotes"]:
                if f in t["footnotes"]:
                    conds.append({"name": f"{tname}_FN_{f}", "timeline": tname, "marker": None, "label": f"{tname} footnote {f}",
                                  "text": t["footnotes"][f], "applies_to": aname})
        for mk, txt in t["footnotes"].items():
            col_notes = "".join(c.get("notes") or "" for c in cols)
            if not re.fullmatch(r"[a-z]{1,2}", mk) or mk in col_notes or any(mk in "".join(c or "" for c in rr["cells"]) for rr in rows):
                conds.append({"name": f"{tname}_FN_{'SYM' + str(MARK_SYMBOLS.find(mk)) if mk in MARK_SYMBOLS else mk}",
                              "timeline": tname, "marker": mk, "label": f"{tname} footnote {mk}", "text": txt})
        soas.append({"sheet": sheet, "timeline": tname, "description": t["title"], "columns": cols, "rows": rows})
    # merge footnote conditions that share name (same footnote on several rows)
    merged = OrderedDict()
    for c in conds:
        if c["name"] in merged:
            if c.get("applies_to"):
                prev = merged[c["name"]].get("applies_to")
                merged[c["name"]]["applies_to"] = "; ".join(x for x in OrderedDict.fromkeys(filter(None, [prev, c["applies_to"]])))
        else:
            merged[c["name"]] = c
    # categories referenced by activities but empty -> drop
    for a in acts.values():
        if a["bc_category"] and a["bc_category"] not in cats:
            a["bc_category"] = None
    data["Encounters"] = list(encs.values())
    data["Activities"] = list(acts.values())
    data["BiomedicalConcepts"] = list(bcs.values())
    data["BCSurrogates"] = list(surrogates.values())
    data["BCCategories"] = list(cats.values())
    data["Conditions"] = list(merged.values())
    data["Timelines"] = tl_rows
    data["SoA"] = soas


# -------------------------------------------------------------------------------- report
def _report(data, log):
    bcs = data.get("BiomedicalConcepts", [])
    by = Counter(b["method"] for b in bcs)
    rev = [b for b in bcs if b["review_status"] != "auto-accepted"]
    acts = data.get("Activities", [])
    lines = ["== extraction report ==", *[f"  {l}" for l in log],
             f"  activities: {len(acts)} ({sum(a['review_status'] == 'auto-accepted' for a in acts)} auto-accepted, "
             f"{sum(a['review_status'] != 'auto-accepted' for a in acts)} need review)",
             f"  biomedical concepts: {len(bcs)} by method {dict(by)}; {len(rev)} flagged for review",
             f"  surrogates: {len(data.get('BCSurrogates', []))} -> " + ", ".join(s["name"] for s in data.get("BCSurrogates", []))]
    return "\n".join(lines)
