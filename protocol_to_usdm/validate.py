"""Structural + referential validation of a USDM v4 JSON file against data/usdm_v4_model.json.

This is a fast pre-check for the build loop. It is NOT a substitute for the CDISC CORE DDF rules
(https://github.com/cdisc-org/cdisc-rules-engine) which remain the authoritative conformance check.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from .model import load_spec

PRIMS = {"string": str, "boolean": bool, "integer": int, "float": (int, float), "date": str}


class Issue(dict):
    def __str__(self):
        return f"[{self['severity']}] {self['path']}: {self['message']}"


def validate(doc: dict, spec: dict | None = None) -> list[Issue]:
    spec = spec or load_spec()
    issues: list[Issue] = []

    def add(sev, path, msg):
        issues.append(Issue(severity=sev, path=path, message=msg))

    for k in ("study", "usdmVersion"):
        if k not in doc:
            add("error", "$", f"wrapper missing '{k}'")
    objs: dict[str, dict] = {}
    refs: list[tuple[str, str, str, list]] = []
    dup = Counter()

    def walk(o, path):
        if isinstance(o, list):
            for i, x in enumerate(o):
                walk(x, f"{path}[{i}]")
            return
        if not isinstance(o, dict):
            return
        it = o.get("instanceType")
        if it is None:
            for k, v in o.items():
                walk(v, f"{path}.{k}")
            return
        if it not in spec:
            add("error", path, f"unknown instanceType '{it}'")
            return
        oid = o.get("id")
        if oid:
            dup[oid] += 1
            objs[oid] = o
        attrs = spec[it]["attributes"]
        for k in o:
            if k not in attrs:
                add("error", path, f"{it}: attribute '{k}' not in USDM v4 model")
        for name, a in attrs.items():
            v = o.get(name)
            p = f"{path}.{name}"
            many = a["card"].endswith("*") or a["card"] == "0..2"
            required = a["card"].startswith("1")
            if name not in o:
                if required:
                    add("error", p, f"required attribute missing ({a['card']})")
                continue
            if required and (v is None or v == [] or v == ""):
                add("error", p, f"required attribute empty ({a['card']})")
                continue
            if v is None:
                continue
            if many and not isinstance(v, list):
                add("error", p, "expected a list")
                continue
            if not many and isinstance(v, list):
                add("error", p, "expected a single value, got a list")
                continue
            vals = v if many else [v]
            if a["rel"] == "R":
                for x in vals:
                    if not isinstance(x, str):
                        add("error", p, "reference must be an id string")
                    else:
                        refs.append((p, x, it, a["types"]))
                continue
            for i, x in enumerate(vals):
                types = a["types"]
                if all(t in PRIMS or t == "" for t in types):
                    if not any(isinstance(x, PRIMS[t]) for t in types if t in PRIMS) and not (types == ["float"] and isinstance(x, int)):
                        add("warning", p, f"expected {types}, got {type(x).__name__}")
                    if types == ["date"] and isinstance(x, str) and not re.match(r"^\d{4}-\d{2}-\d{2}", x):
                        add("error", p, "date must be YYYY-MM-DD")
                    continue
                if isinstance(x, dict):
                    xt = x.get("instanceType")
                    if xt not in types and not (xt == "Quantity" and "QuantityRange" in types) and not (xt == "Range" and "QuantityRange" in types):
                        add("error", p, f"expected {types}, got {xt}")
                    walk(x, f"{p}[{i}]" if many else p)
                else:
                    add("error", p, f"expected object {types}")
        if it == "Code":
            if o.get("codeSystem") == "http://www.cdisc.org" and not re.match(r"^C\d+$", str(o.get("code"))):
                add("warning", path, f"CDISC code '{o.get('code')}' is not a C-code")

    walk(doc.get("study"), "$.study")
    for oid, n in dup.items():
        if n > 1:
            add("error", oid, f"duplicate id ({n} times)")
    for p, x, src, types in refs:
        tgt = objs.get(x)
        if tgt is None:
            add("error", p, f"dangling reference '{x}'")
        elif tgt["instanceType"] not in types and not (types == ["PopulationDefinition"]) and not (
                "StudyDesign" in types and tgt["instanceType"].endswith("StudyDesign")):
            add("error", p, f"reference '{x}' is {tgt['instanceType']}, expected {types}")
    _semantic(doc, objs, add)
    return issues


def _semantic(doc, objs, add):
    by_type = defaultdict(list)
    for o in objs.values():
        by_type[o["instanceType"]].append(o)
    # prev/next chains
    for t, items in by_type.items():
        for o in items:
            n = o.get("nextId")
            if n and objs.get(n, {}).get("previousId") != o["id"]:
                add("error", o["id"], f"nextId {n} does not point back via previousId")
    # timelines
    tls = by_type.get("ScheduleTimeline", [])
    if tls and sum(1 for t in tls if t.get("mainTimeline")) != 1:
        add("error", "ScheduleTimeline", "exactly one timeline must have mainTimeline=true")
    for t in tls:
        inst = {i["id"] for i in t.get("instances", [])}
        if t.get("entryId") not in inst:
            add("error", t["id"], "entryId is not one of the timeline's instances")
        for tm in t.get("timings", []):
            for k in ("relativeFromScheduledInstanceId", "relativeToScheduledInstanceId"):
                if tm.get(k) and tm[k] not in inst:
                    add("error", tm["id"], f"{k} points outside its timeline")
            if not re.match(r"^P(\d+D|T?\d+[HMS]|\d+W)", str(tm.get("value"))):
                add("warning", tm["id"], f"timing value '{tm.get('value')}' is not an ISO 8601 duration")
        if not any(i.get("timelineExitId") for i in t.get("instances", [])):
            add("warning", t["id"], "no instance leads to a timeline exit")
    # design completeness
    for d in by_type.get("InterventionalStudyDesign", []):
        have = {(c["armId"], c["epochId"]) for c in d.get("studyCells", [])}
        for a in d.get("arms", []):
            for e in d.get("epochs", []):
                if (a["id"], e["id"]) not in have:
                    add("warning", d["id"], f"no StudyCell for arm '{a['name']}' x epoch '{e['name']}'")
        scheduled = {aid for t in d.get("scheduleTimelines", []) for i in t.get("instances", []) for aid in i.get("activityIds", [])}
        for a in d.get("activities", []):
            if not (a.get("biomedicalConceptIds") or a.get("bcSurrogateIds") or a.get("bcCategoryIds") or a.get("timelineId")):
                add("warning", a["id"], f"activity '{a['name']}' has no BC / surrogate / category")
            if a["id"] not in scheduled:
                add("warning", a["id"], f"activity '{a['name']}' is not scheduled in any timeline")
    for bc in by_type.get("BiomedicalConcept", []):
        if not bc.get("properties"):
            add("warning", bc["id"], f"BC '{bc['name']}' has no properties (not a CDISC library BC?)")


def summarize(issues):
    c = Counter(i["severity"] for i in issues)
    return f"{c.get('error', 0)} error(s), {c.get('warning', 0)} warning(s)"
