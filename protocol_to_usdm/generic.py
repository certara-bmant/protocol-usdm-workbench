"""Template-agnostic fall-backs for protocols that do not follow the 'classic synopsis' layout -
in particular TransCelerate Common Protocol Template (CPT) / ICH M11-style documents:

    1.1 Synopsis (Overall Design key/value table) · 1.3 Schedule of Activities
    3 Objectives and Endpoints (two-column table) · 5.1 Inclusion / 5.2 Exclusion Criteria
    6.1 Study Intervention(s) Administered · Appendix: Abbreviations / Laboratory tests

`fill_gaps` only fills what the primary extractor left empty (or marked as a default), so the two
strategies can run on every protocol.  Every value set here is noted in the extraction log.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from datetime import datetime

HEAD = re.compile(r"(?m)^(\d{1,2}(?:\.\d{1,2}){0,3})\s+([A-Z][^\n]{2,110}?)\s*$")
BULLET = re.compile(r"^\s*[•●▪\-–‒∀]\s*")


def oneline(s):
    return re.sub(r"\s+", " ", s or "").strip()


def slug(s):
    return re.sub(r"\W+", "_", str(s).upper()).strip("_")


# ------------------------------------------------------------------ section index
def headings(doc):
    """numbered headings in the body (TOC lines with dot leaders / trailing page numbers excluded)"""
    out = []
    for m in HEAD.finditer(doc.text):
        title = m.group(2)
        if re.search(r"\.{4,}|·{3,}", title) or re.search(r"\s\d{1,3}$", title) or len(title.split()) > 14:
            continue
        out.append((m.start(), m.end(), m.group(1), title.strip()))
    return out


def section(doc, title_pat, heads=None):
    """text of the first body section whose title matches, up to the next heading of the same/higher level"""
    heads = heads if heads is not None else headings(doc)
    for i, (s, e, num, title) in enumerate(heads):
        if re.search(title_pat, title, re.I):
            lvl = num.count(".")
            end = len(doc.text)
            for s2, _, n2, _ in heads[i + 1:]:
                if n2.count(".") <= lvl:
                    end = s2
                    break
            section.last_start = e
            return doc.text[e:end], num, title
    return None, None, None


def page_of(doc, offset):
    n = 0
    for i, p in enumerate(doc.pages):
        n += len(p) + 1
        if offset < n:
            return i
    return len(doc.pages) - 1


# ------------------------------------------------------------------ helpers
def _date(s):
    s = s.strip().replace(",", "")
    for fmt in ("%d %B %Y", "%d %b %Y", "%d-%b-%Y", "%d-%B-%Y", "%B %d %Y", "%Y-%m-%d", "%d%b%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _field(text, label, multiline_until=None):
    m = re.search(rf"(?mi)^\s*{label}\s*:?\s*(.*)$", text or "")
    if not m:
        return None
    val = m.group(1).strip()
    if multiline_until:
        rest = text[m.end():]
        stop = re.search(multiline_until, rest, re.M)
        if stop:
            val = oneline(val + " " + rest[:stop.start()])
    return val or None


def numbered_items(text):
    """'1. ...' lists; unnumbered short Title-Case lines between items are sub-headings, not text"""
    lines = text.split("\n")
    items, cur = [], None
    for i, line in enumerate(lines):
        m = re.match(r"^\s*(\d{1,2})[.)]\s*([A-Z(\[\"'].*)$", line)
        if m and (cur is None or int(m.group(1)) == cur[0] + 1):
            cur = [int(m.group(1)), m.group(2)]
            items.append(cur)
            continue
        if cur is None or not line.strip():
            continue
        nxt = next((l for l in lines[i + 1:] if l.strip()), "")
        if (len(line.strip()) < 45 and not re.search(r"[.;:,]$", line.strip()) and line.strip()[:1].isupper()
                and re.match(r"^\s*\d{1,2}[.)]\s", nxt)):
            continue  # sub-heading such as 'Demographics'
        cur[1] += " " + line.strip()
    return [(n, oneline(t)) for n, t in items]


# ------------------------------------------------------------------ main entry
def fill_gaps(doc, data, tables, log):
    heads = headings(doc)
    syn, _, _ = section(doc, r"^Synopsis$", heads)
    syn = syn or ""
    p12 = "\n".join(doc.pages[:3])
    _identity(doc, data, syn, p12, log)
    _design(doc, data, syn, log)
    if not data.get("Objectives"):
        _objectives(doc, data, heads, log)
    if not data.get("Eligibility"):
        _eligibility(doc, data, heads, log)
    if not data.get("Products"):
        _interventions(doc, data, syn, heads, log)
    _population(doc, data, syn, heads, log)


def _identity(doc, data, syn, p12, log):
    S = data["Study"]
    titles = data.setdefault("Titles", [])
    if not titles:
        t = _field(p12, r"(?:Official )?Protocol Title", r"^\s*(?:Short Title|Protocol Number|NCT|Acronym|Document Date|Sponsor|Compound)") \
            or _field(syn, r"Protocol Title", r"^\s*(?:Short Title|Acronym|Hypotheses)")
        if t:
            titles.append({"type": "Official Study Title", "text": oneline(t)})
            S["description"] = S.get("description") or oneline(t)[:300]
            log.append("generic: official title from 'Protocol Title'")
        st = _field(syn or p12, r"Short Title")
        if st:
            titles.append({"type": "Brief Study Title", "text": st})
        ac = _field(syn or p12, r"Acronym")
        if ac and not re.match(r"(?i)not applicable|n/?a|none", ac):
            titles.append({"type": "Study Acronym", "text": ac})
    orgs, ids = data.setdefault("Organizations", []), data.setdefault("Identifiers", [])
    sponsor = _field(p12, r"Sponsor Name", r"^\s*\S.*:\s*$|^\(|^Legal") or _field(p12, r"Sponsor")
    if sponsor and (S.get("sponsor") in (None, "Sponsor")):
        sponsor = re.sub(r"\s*\(hereafter.*$", "", oneline(sponsor)).strip(" ,")
        for o in orgs:
            if o["name"] == S.get("sponsor"):
                o["name"] = o["identifier"] = sponsor
        for i in ids:
            if i["organization"] == S.get("sponsor"):
                i["organization"] = sponsor
        if not any(o["name"] == sponsor for o in orgs):
            orgs.insert(0, {"name": sponsor, "label": "Sponsor", "type": "Drug Company", "identifier_scheme": "N/A", "identifier": sponsor})
        S["sponsor"] = sponsor
        log.append(f"generic: sponsor '{sponsor}'")
    pn = _field(p12, r"Protocol(?:/Amendment)? (?:Number|No\.?)")
    cmpd = _field(p12, r"Compound (?:Number|Name)") or _field(p12, r"PRODUCT")
    if cmpd:
        cmpd = cmpd.split()[0]
    if pn and (not S.get("name") or S["name"] in ("STUDY", pn.split()[0]) or (cmpd and cmpd not in S["name"])):
        pn = pn.split()[0]
        old = S.get("name")
        ids[:] = [i for i in ids if i["text"] != old]
        base = re.match(r"^(\d{3})-(\d{2})$", pn)
        sid = f"{cmpd}-{base.group(1)}" if (cmpd and base) else (f"{cmpd}-{pn}" if cmpd else pn)
        S["name"], S["label"] = sid, f"{sid} (protocol/amendment {pn})"
        if base:
            S["version"] = S.get("version") or base.group(2)
            S["version_rationale"] = f"Protocol amendment {base.group(2)}" if base.group(2) != "00" else "Original protocol"
        if not any(i["text"] == sid for i in ids):
            ids.insert(0, {"text": sid, "organization": S.get("sponsor") or "Sponsor"})
        log.append(f"generic: study id '{sid}' from Protocol Number '{pn}'" + (f" + compound '{cmpd}'" if cmpd else ""))
    eud = re.search(r"EudraCT(?:\s*(?:No\.?|Number))?\s*:?\s*(\d{4}-\d{6}-\d{2})", doc.text)
    if eud and not any(eud.group(1) in i["text"] for i in ids):
        orgs.append({"name": "EudraCT", "label": "EU Clinical Trials Database", "type": "Clinical Study Registry",
                     "identifier_scheme": "N/A", "identifier": "EudraCT"})
        ids.append({"text": eud.group(1), "organization": "EudraCT"})
    ind = re.search(r"\bIND(?:\s*(?:No\.?|Number))?\s*:?\s*(\d{5,6})\b", p12)
    if ind and not any(ind.group(1) in i["text"] for i in ids):
        orgs.append({"name": "US FDA", "type": "Government Institute", "identifier_scheme": "N/A", "identifier": "FDA"})
        ids.append({"text": "IND " + ind.group(1), "organization": "US FDA"})
    if not S.get("approval_date"):
        d = _field(p12, r"Approval Date") or _field(p12, r"Document Date") or _field(p12, r"Date of (?:Protocol|Document)")
        if d and _date(d.split("  ")[0]):
            S["approval_date"] = _date(d.split("  ")[0])
    if not S.get("phase"):
        ph = _field(syn, r"Study Phase") or _field(syn, r"Phase(?: of Development)?") or ""
        m = re.search(r"Phase\s*([0-4]|I{1,3}V?|IV)\s*([ab])?(?:\s*/\s*([0-4]|I{1,3}))?", ph + " " + " ".join(t["text"] for t in data.get("Titles", [])), re.I)
        if m:
            raw = f"phase {m.group(1)}{('/' + m.group(3)) if m.group(3) else ''}{(m.group(2) or '').lower()}"
            from .ct import CTError, Terminology
            try:
                S["phase"] = Terminology().lookup("phase", raw)[1]
            except CTError:
                try:
                    S["phase"] = Terminology().lookup("phase", raw.rstrip("ab"))[1]
                    log.append(f"REVIEW phase '{raw}' not in CT; mapped to {S['phase']}")
                except CTError:
                    S["phase"] = raw
    S["document_name"] = S.get("document_name") if S.get("name") not in (None, "STUDY") else S.get("document_name")
    if S.get("document_name", "").startswith("Clinical Study Protocol") and S.get("name"):
        S["document_name"] = f"{S['name']} Clinical Study Protocol"
    S["document_version"] = S.get("document_version") or S.get("version")


def _design(doc, data, syn, log):
    S = data["Study"]
    kv = {}
    for label, key in (("Study Phase", "phase"), ("Study Blinding", "blinding"), ("Masking", "masking"),
                       ("Intervention Model", "model"), ("Study Type", "type"), ("Type of Control", "control"),
                       ("Primary Purpose", "purpose"), ("Population", "population"), ("Indication", "indication")):
        v = _field(syn, re.escape(label))
        if v:
            kv[key] = v
    if not kv:
        return
    b = (kv.get("blinding") or "").lower()
    if b:
        S["blinding"] = ("Double Blind Study" if "double" in b or "observer" in b else "Single Blind Study" if "single" in b
                         else "Open Label Study" if "open" in b else S.get("blinding"))
    mo = (kv.get("model") or "").lower()
    if mo:
        S["model"] = ("Parallel Study" if "parallel" in mo else "Crossover Study" if "cross" in mo
                      else "Single Group Study" if "single" in mo else "Group Sequential Design" if "sequential" in mo else S.get("model"))
    if kv.get("type"):
        S["study_type"] = "Observational Study" if "observ" in kv["type"].lower() else "Interventional Study"
    ch = []
    if re.search(r"random", " ".join(t["text"] for t in data.get("Titles", [])) + syn, re.I):
        ch.append("Randomized Controlled Clinical Trial")
    if re.search(r"single[- ](?:site|cent(?:er|re))", syn + " " + (section(doc, r"^Overall Design$")[0] or ""), re.I):
        ch.append("Single-Center Study")
    if ch:
        S["characteristics"] = "; ".join(ch)
    txt = (" ".join(t["text"] for t in data.get("Titles", [])) + " " + syn).lower()
    subs = [k for k, w in (("Safety Study", "safety"), ("Tolerability Study", "tolerab"), ("Pharmacokinetic Study", "pharmacokinetic"),
                           ("Immunogenicity Study", "immunogenicity"), ("Dose Response Study", "dose-finding")) if w in txt]
    S["sub_types"] = S.get("sub_types") or ("; ".join(subs) or None)
    if kv.get("indication") and not S.get("therapeutic_areas"):
        S["therapeutic_areas"] = kv["indication"][:60]
    od = section(doc, r"^Overall Design$")[0]
    if od and not S.get("design_description"):
        S["design_description"] = oneline(od)[:1500]
    rat = section(doc, r"Rationale for (?:Study )?Design|Scientific Rationale")[0]
    if rat and S.get("design_rationale") in (None, "", "See protocol"):
        S["design_rationale"] = oneline(rat)[:1500]
    log.append("generic: design from synopsis table (" + ", ".join(f"{k}={v[:25]}" for k, v in kv.items() if k != "population") + ")")


def _population(doc, data, syn, heads, log):
    P = data.setdefault("Population", {})
    pop = _field(syn, r"Population") or ""
    sp, _, _ = section(doc, r"^Study Population$", heads)
    src = pop + " " + (sp or "")[:800] + " " + " ".join(t["text"] for t in data.get("Titles", []))
    if P.get("min_age") in (None, ""):
        m = re.search(r"(\d{1,2})\s*(?:-|–|to|through)\s*(\d{2,3})\s*years", src, re.I)
        if m:
            P["min_age"], P["max_age"], P["age_unit"] = int(m.group(1)), int(m.group(2)), "Year"
    if P.get("planned_enrollment") in (None, ""):
        m = re.search(r"[Aa]pproximately (\d+) participants will be (?:allocated|randomized|enrolled|randomised)", doc.text) \
            or re.search(r"Total (\d+) participants", doc.text)
        if m:
            P["planned_enrollment"] = int(m.group(1))
    if not P.get("description") and pop:
        P["description"] = pop
    if re.search(r"Healthy Volunteers:\s*No", doc.text):
        P["healthy"] = "N"
    elif not P.get("healthy"):
        P["healthy"] = "Y" if re.search(r"healthy", src, re.I) else "N"
    P.setdefault("name", "POP")
    P["sex"] = P.get("sex") or ("Male" if re.search(r"\bmale participants only\b", src, re.I) else "Female; Male")


# ------------------------------------------------------------------ objectives / endpoints (two-column table)
LEVELS = {"primary": "Primary", "secondary": "Secondary", "tertiary": "Exploratory", "exploratory": "Exploratory",
          "tertiary/exploratory": "Exploratory", "other": "Exploratory"}


def _objectives(doc, data, heads, log):
    txt, num, title = section(doc, r"Objectives.*Endpoints|Endpoints.*Objectives", heads)
    if txt is None:
        log.append("REVIEW objectives/endpoints: no 'Objectives and Endpoints' section found")
        return
    start = section.last_start
    p0 = page_of(doc, start)
    p1 = page_of(doc, start + len(txt))
    objs, eps = [], []
    split_x, level, cur_o, cur_e = None, None, None, None
    stop = False
    for pi in range(p0, p1 + 1):
        ws = doc.words(pi)
        lines = []
        for w in sorted(ws, key=lambda w: w["top"]):
            if lines and abs(w["top"] - lines[-1][0]) <= 3:
                lines[-1][1].append(w)
            else:
                lines.append([w["top"], [w]])
        bullets = sorted(w["x0"] for w in ws if w["text"] in ("•", "●", "▪", "-", "–"))
        for _, lw in lines:
            lw.sort(key=lambda w: w["x0"])
            text = " ".join(w["text"] for w in lw)
            if doc._key(text) in doc.boiler:
                continue
            if pi == p1 and HEAD.match(text) and not text.startswith(num):
                if re.match(r"^\d", text) and re.search(r"[A-Z]{4,}", text):
                    stop = True
                    break
            hdr = [w for w in lw if re.match(r"(?i)^endpoints?$", w["text"])]
            ohdr = [w for w in lw if re.match(r"(?i)^objectives?$", w["text"])]
            if hdr and ohdr and len(lw) <= 6:
                split_x = (ohdr[0]["x1"] + hdr[0]["x0"]) / 2
                right_b = [x for x in bullets if ohdr[0]["x1"] < x < hdr[0]["x1"]]
                if right_b:
                    split_x = min(right_b) - 3
                continue
            if split_x is None:
                continue
            key = text.strip().lower().rstrip(":")
            if key in LEVELS or re.fullmatch(r"(primary|secondary|tertiary|exploratory)(\s*/\s*exploratory)?( objectives?)?", key):
                level = LEVELS.get(key.split()[0].split("/")[0], "Exploratory") if key.split()[0] in ("primary", "secondary") \
                    else "Exploratory"
                continue
            left = " ".join(w["text"] for w in lw if w["x1"] <= split_x + 2)
            right = " ".join(w["text"] for w in lw if w["x0"] >= split_x - 1)
            if left:
                if BULLET.match(left) or cur_o is None:
                    body = BULLET.sub("", left)
                    if re.match(r"(?i)^hypothes", body) and cur_o is not None:
                        cur_o["description"] = (cur_o.get("description") or "") + body
                        cur_o["_in_hyp"] = True
                    else:
                        cur_o = {"level": level or "Primary", "text": body, "description": None}
                        objs.append(cur_o)
                elif cur_o.get("_in_hyp"):
                    cur_o["description"] += " " + left
                else:
                    cur_o["text"] += " " + left
            if right:
                if BULLET.match(right) or cur_e is None:
                    cur_e = {"level": level or "Primary", "text": BULLET.sub("", right), "objective": cur_o}
                    eps.append(cur_e)
                else:
                    cur_e["text"] += " " + right
        if stop:
            break
    if not objs:
        log.append("REVIEW objectives/endpoints: section found but no two-column table could be read")
        return
    rows, counters = [], {}
    for o in objs:
        lv = o["level"]
        counters[lv] = counters.get(lv, 0) + 1
        o["name"] = f"OBJ_{lv[:3].upper()}{counters[lv]}"
        rows.append({"name": o["name"], "label": f"{lv} objective {counters[lv]}", "level": lv,
                     "text": oneline(o["text"]), "description": oneline(o.get("description")) or None})
    erows, ecount = [], {}
    for e in eps:
        o = e["objective"] or objs[0]
        txt = oneline(e["text"])
        ot = (o.get("text") or "").lower() + " " + txt.lower()
        purpose = ("Safety" if re.search(r"safety|adverse|tolerab", ot) else
                   "Pharmacokinetic" if re.search(r"concentration|pharmacokinetic|\bpk\b|auc|cmax", ot) and "viral load" not in ot else
                   "Immunogenicity" if re.search(r"antibod|titer|titre|immun|elispot", ot) else "Efficacy")
        ecount[e["level"]] = ecount.get(e["level"], 0) + 1
        erows.append({"objective": o["name"], "name": f"EP_{e['level'][:3].upper()}{ecount[e['level']]}", "label": txt[:60],
                      "level": e["level"], "purpose": purpose, "text": txt})
    data["Objectives"], data["Endpoints"] = rows, erows
    log.append(f"generic: objectives {len(rows)}, endpoints {len(erows)} from two-column table in section {num} '{title}'")


# ------------------------------------------------------------------ eligibility
def _eligibility(doc, data, heads, log):
    rows = []
    for cat, pat, p in (("Inclusion Criteria", r"^Inclusion Criteria$", "INC"), ("Exclusion Criteria", r"^Exclusion Criteria$", "EXC")):
        txt, num, title = section(doc, pat, heads)
        if txt is None:
            log.append(f"REVIEW {cat}: no section found")
            continue
        items = numbered_items(txt)
        for n, t in items:
            rows.append({"identifier": f"{p[0]}{n}", "category": cat, "name": f"{p}{n:02d}", "label": f"{cat.split()[0]} {n}", "text": t})
        log.append(f"generic: {len(items)} {cat.lower()} from section {num}")
    data["Eligibility"] = rows


# ------------------------------------------------------------------ interventions
def _interventions(doc, data, syn, heads, log):
    S = data["Study"]
    cmpd = None
    m = re.search(r"(?mi)^\s*(?:Compound (?:Number|Name)|PRODUCT)\s*:?\s*(\S+)", "\n".join(doc.pages[:3]))
    if m:
        cmpd = m.group(1)
    sec, _, _ = section(doc, r"Study Intervention\(?s?\)? Administered|Investigational Product", heads)
    scope = (syn or "") + "\n" + (sec or "")
    if not cmpd:
        log.append("REVIEW interventions: compound name not found - fill Interventions/Administrations")
        return
    low = scope.lower()
    route = ("Intravenous Route of Administration" if re.search(r"\biv\b|intravenous", low)
             else "Intramuscular Route of Administration" if re.search(r"\bim\b|intramuscular", low)
             else "Oral Route of Administration" if re.search(r"\boral\b|tablet|capsule", low) else None)
    bio = bool(re.search(r"biolog|monoclonal|antibod|vaccine", low))
    form = ("Injectable Solution Dosage Form" if "solution" in low else "Injectable Suspension Dosage Form" if "suspension" in low
            else "Tablet Dosage Form" if "tablet" in low else None)
    doses = []
    m = re.search(r"(\d+(?:\.\d+)?(?:\s*(?:mg)?\s*,\s*\d+(?:\.\d+)?)*\s*,?\s*(?:mg\s*)?(?:and|or)\s*\d+(?:\.\d+)?)\s*(mg|µg|mcg|g)\b", scope)
    unit = "Milligram"
    if m:
        doses = re.findall(r"\d+(?:\.\d+)?", m.group(1))
        unit = {"mg": "Milligram", "µg": "Microgram", "mcg": "Microgram"}.get(m.group(2), "Milligram")
    prods = [{"name": f"{cmpd} product", "label": cmpd, "dose_form": form, "designation": "Investigational Medicinal Product",
              "sourcing": None}]
    intvs = [{"name": cmpd, "label": cmpd, "description": f"{cmpd} ({route or 'route not found'})",
              "role": "Protocol Agent", "type": "Biological Agent" if bio else "Pharmacologic Substance"}]
    adms = [{"intervention": cmpd, "name": f"{cmpd} {d} {'mg' if unit == 'Milligram' else 'µg'}",
             "label": f"{cmpd} {d} {'mg' if unit == 'Milligram' else 'µg'}", "dose_value": d, "dose_unit": unit,
             "route": route, "frequency": "Once" if re.search(r"single|once", low) else None, "product": f"{cmpd} product",
             "duration_text": "Single dose" if re.search(r"single", low) else None} for d in doses] or \
        [{"intervention": cmpd, "name": f"{cmpd} administration", "route": route, "product": f"{cmpd} product"}]
    per_dose = len(doses) > 1 and re.search(r"randomi[sz]ed to receive|\b1:1(?::1)+\b|one of \d+ dose levels|1 of \d+ dose levels", scope + doc.text[:60000], re.I)
    u = "mg" if unit == "Milligram" else "µg"
    if per_dose:
        arms = [{"name": f"{cmpd} {d} {u}", "label": f"{cmpd} {d} {u}", "description": f"{cmpd} {d} {u} arm", "type": "Investigational Arm",
                 "data_origin_type": "Data Generated Within Study", "data_origin_description": "Data collected in study",
                 "_intv": cmpd} for d in doses]
    else:
        arms = [{"name": cmpd, "label": cmpd, "description": f"{cmpd} arm", "type": "Investigational Arm",
                 "data_origin_type": "Data Generated Within Study", "data_origin_description": "Data collected in study", "_intv": cmpd}]
    if re.search(r"placebo", low + " ".join(t["text"] for t in data.get("Titles", [])).lower()):
        prods.append({"name": "Placebo product", "label": "Placebo", "dose_form": form, "designation": "Investigational Medicinal Product",
                      "sourcing": None})
        intvs.append({"name": "Placebo", "label": "Placebo", "description": "Matching placebo", "role": "Placebo",
                      "type": "Pharmacologic Substance"})
        adms.append({"intervention": "Placebo", "name": "Placebo", "label": "Placebo", "route": route,
                     "frequency": "Once" if re.search(r"single|once", low) else None, "product": "Placebo product"})
        arms.append({"name": "Placebo", "label": "Placebo", "description": "Placebo arm", "type": "Placebo Control Arm",
                     "data_origin_type": "Data Generated Within Study", "data_origin_description": "Data collected in study",
                     "_intv": "Placebo"})
    others = sorted({x.strip() for x in re.findall(r"(?m)^([A-Z][\w-]*(?: [A-Z0-9][\w-]*){1,3})\s+(?:virus|challenge)", sec or "")})
    if others or re.search(r"challenge (?:virus|agent|strain)|inoculat", low):
        log.append("REVIEW interventions: non-investigational / challenge agent mentioned (e.g. challenge virus) - add manually if it should be modelled")
    data["Products"], data["Interventions"], data["Administrations"] = prods, intvs, adms
    epochs = [e["name"] for e in data.get("Epochs", [])]
    treat = [e["name"] for e in data.get("Epochs", []) if e["type"] == "Treatment Epoch"][:1]
    els = [{"name": "EL_" + slug(e), "label": e, "description": f"{e} period"} for e in epochs]
    for a in arms:
        els.append({"name": "EL_" + slug(a["name"]), "label": f"{a['name']} administration", "interventions": a["_intv"]})
    data["Elements"] = els
    cells = []
    for a in arms:
        for e in epochs:
            el = ["EL_" + slug(e)]
            if e in treat:
                el.append("EL_" + slug(a["name"]))
            cells.append({"arm": a["name"], "epoch": e, "elements": "; ".join(el)})
    data["Cells"] = cells
    for a in arms:
        a.pop("_intv", None)
    data["Arms"] = arms
    if per_dose:
        log.append(f"generic: one arm per dose level ({len(arms)} arms) - randomised to one of several doses; check against the schema")
    log.append(f"generic: interventions {', '.join(i['name'] for i in intvs)}; doses {', '.join(doses) or 'not found'}; route {route}")


# ------------------------------------------------------------------ lab tests appendix (free-form table)
PANEL_HEADS = [("Hematology", r"ha?ematology"), ("Chemistry", r"(?:clinical |blood |serum )?(?:bio)?chemistry"),
               ("Coagulation", r"coagulation"), ("Urinalysis", r"routine\b|urinalysis"), ("Other", r"other\b|serology|virology")]


def lab_panels(doc, lib, log):
    """Find the laboratory-tests table and term-spot analytes per panel using the BC library vocabulary."""
    idx = None
    for i, t in enumerate(doc.pages):
        if re.search(r"(?im)^\s*Table\s*\d+\s+.*Laborator(?:y|ies).*(?:Assessments|Tests|Parameters)", t) or \
                re.search(r"(?im)^\s*(?:\d+(?:\.\d+)*\s+)?(?:Appendix \d+:\s*)?Clinical Laboratory Tests\s*$", t):
            if re.search(r"(?i)hematology|haematology", t) and re.search(r"(?i)chemistry", t):
                idx = i
                break
    if idx is None:
        return {}
    text = doc.pages[idx] + ("\n" + doc.pages[idx + 1] if idx + 1 < len(doc.pages) else "")
    cut = re.search(r"(?m)^NOTES?:", text)
    nxt = re.search(r"(?m)^\d+(?:\.\d+)+\s+[A-Z]", text[200:])
    end = cut.start() if cut else (nxt.start() + 200 if nxt else len(text))
    text = text[:end]
    # split into panel chunks at row headers (first word of a line)
    marks = []
    for name, pat in PANEL_HEADS:
        for m in re.finditer(rf"(?im)^\s*{pat}\b", text):
            marks.append((m.start(), name))
    marks.sort()
    chunks = OrderedDict()
    for k, (pos, name) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(text)
        chunks[name] = chunks.get(name, "") + " " + text[pos:end]
    keys = {k for k in lib.synonyms if len(k) > 2}
    urine_keys = {k[6:] for k in keys if k.startswith("urine ") and len(k) > 7} | {"ph"}
    names = {n.lower() for n in (e.name for e in lib.entries.values())}
    from .bc_library import norm
    panels = OrderedDict()
    for name, chunk in chunks.items():
        vocab = sorted(keys | names | set(lib.evs_cache) | (urine_keys if name == "Urinalysis" else set()), key=len, reverse=True)
        nchunk = " " + norm(chunk) + " "
        found = []
        for term in vocab:
            nt = " " + norm(term) + " "
            if nt in nchunk:
                found.append(term)
                nchunk = nchunk.replace(nt, " ")
        if found:
            panels[name] = [(f, None) for f in found]
    if panels:
        log.append(f"generic: lab panels from p{idx + 1} by term-spotting: " + ", ".join(f"{k} ({len(v)})" for k, v in panels.items()))
    return panels
