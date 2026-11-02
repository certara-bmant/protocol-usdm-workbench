"""USDM v4 JSON -> StudyData (Excel template form).  Used for round-trip testing and for editing an
existing USDM file in Excel."""
from __future__ import annotations

import re

from .build import EXT


def _dec(c):
    if not c:
        return None
    if c.get("instanceType") == "AliasCode":
        c = c.get("standardCode") or {}
    return c.get("decode")


def _decs(cs):
    return "; ".join(_dec(c) for c in cs or [] if _dec(c)) or None


def _days(iso):
    """ISO duration -> days (int) or a clock string like '5m' / '2h' for sub-day values."""
    if not iso:
        return None
    m = re.match(r"^P(\d+)D$", iso)
    if m:
        return int(m.group(1))
    m = re.match(r"^PT(\d+(?:\.\d+)?)([HM])$", iso)
    if m:
        return f"{float(m.group(1)):g}{m.group(2).lower()}"
    return None


def _prov(o, keys):
    out = {}
    for e in o.get("extensionAttributes", []) or []:
        if e.get("url", "").startswith(EXT + "#"):
            k = e["url"].split("#", 1)[1]
            if k in keys:
                out[k] = e.get("valueString")
    return out


def export(doc: dict) -> dict:
    s = doc["study"]
    v = s["versions"][0]
    d = v["studyDesigns"][0]
    ids = {}

    def walk(o):
        if isinstance(o, dict):
            if "id" in o and "instanceType" in o:
                ids[o["id"]] = o
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(doc)
    nm = lambda i: ids[i]["name"] if i in ids else i  # noqa: E731
    sponsor = next((nm(r["organizationIds"][0]) for r in v.get("roles", []) if r.get("organizationIds")
                    and (r.get("code") or {}).get("code") == "C70793"), None)
    docs = s.get("documentedBy", [])
    dv = docs[0]["versions"][0] if docs and docs[0].get("versions") else {}
    appr = next((g["dateValue"] for g in v.get("dateValues", []) if (g.get("type") or {}).get("code") == "C71476"), None)
    data = {
        "Study": {"name": s["name"], "label": s.get("label"), "description": s.get("description"),
                  "version": v.get("versionIdentifier"), "version_rationale": v.get("rationale"), "approval_date": appr,
                  "sponsor": sponsor, "study_type": _dec(d.get("studyType")), "phase": _dec(d.get("studyPhase")),
                  "blinding": _dec(d.get("blindingSchema")), "model": _dec(d.get("model")),
                  "characteristics": _decs(d.get("characteristics")), "sub_types": _decs(d.get("subTypes")),
                  "therapeutic_areas": _decs(v.get("businessTherapeuticAreas")),
                  "design_name": d.get("name"), "design_description": d.get("description"), "design_rationale": d.get("rationale"),
                  "document_name": docs[0]["name"] if docs else None, "document_version": dv.get("version"),
                  "document_status": _dec(dv.get("status")), "document_template": docs[0].get("templateName") if docs else None,
                  "language": (docs[0].get("language") or {}).get("code") if docs else None},
    }
    p = d["population"]

    def age(o):
        r = o.get("plannedAge") or {}
        return ((r.get("minValue") or {}).get("value"), (r.get("maxValue") or {}).get("value"),
                _dec((r.get("minValue") or {}).get("unit")))
    mn, mx, u = age(p)
    data["Population"] = {"name": p["name"], "label": p.get("label"), "description": p.get("description"),
                          "healthy": p.get("includesHealthySubjects"), "min_age": mn, "max_age": mx, "age_unit": u,
                          "planned_enrollment": (p.get("plannedEnrollmentNumber") or {}).get("value"),
                          "planned_completion": (p.get("plannedCompletionNumber") or {}).get("value"),
                          "sex": _decs(p.get("plannedSex"))}
    data["Cohorts"] = []
    for c in p.get("cohorts", []):
        mn, mx, u = age(c)
        data["Cohorts"].append({"name": c["name"], "label": c.get("label"), "description": c.get("description"),
                                "healthy": c.get("includesHealthySubjects"), "min_age": mn, "max_age": mx, "age_unit": u,
                                "planned_enrollment": (c.get("plannedEnrollmentNumber") or {}).get("value"),
                                "sex": _decs(c.get("plannedSex"))})
    data["Titles"] = [{"type": _dec(t["type"]), "text": t["text"]} for t in v.get("titles", [])]
    data["Organizations"] = [{"name": o["name"], "label": o.get("label"), "type": _dec(o["type"]),
                              "identifier_scheme": o.get("identifierScheme"), "identifier": o.get("identifier")}
                             for o in v.get("organizations", [])]
    data["Identifiers"] = [{"text": i["text"], "organization": nm(i["scopeId"])} for i in v.get("studyIdentifiers", [])]
    data["Arms"] = [{"name": a["name"], "label": a.get("label"), "description": a.get("description"), "type": _dec(a["type"]),
                     "data_origin_type": _dec(a.get("dataOriginType")), "data_origin_description": a.get("dataOriginDescription"),
                     "cohorts": "; ".join(nm(x) for x in a.get("populationIds", [])) or None} for a in d.get("arms", [])]
    data["Epochs"] = [{"name": e["name"], "label": e.get("label"), "description": e.get("description"), "type": _dec(e["type"])}
                      for e in d.get("epochs", [])]
    data["Elements"] = [{"name": e["name"], "label": e.get("label"), "description": e.get("description"),
                         "interventions": "; ".join(nm(x) for x in e.get("studyInterventionIds", [])) or None,
                         "start_rule": (e.get("transitionStartRule") or {}).get("text"),
                         "end_rule": (e.get("transitionEndRule") or {}).get("text")} for e in d.get("elements", [])]
    data["Cells"] = [{"arm": nm(c["armId"]), "epoch": nm(c["epochId"]), "elements": "; ".join(nm(x) for x in c["elementIds"])}
                     for c in d.get("studyCells", [])]
    data["Products"] = [{"name": pr["name"], "label": pr.get("label"), "description": pr.get("description"),
                         "dose_form": _dec(pr.get("administrableDoseForm")), "designation": _dec(pr.get("productDesignation")),
                         "sourcing": _dec(pr.get("sourcing"))} for pr in v.get("administrableProducts", [])]
    data["Interventions"], data["Administrations"] = [], []
    for si in v.get("studyInterventions", []):
        data["Interventions"].append({"name": si["name"], "label": si.get("label"), "description": si.get("description"),
                                      "role": _dec(si["role"]), "type": _dec(si["type"])})
        for a in si.get("administrations", []):
            dose = a.get("dose") or {}
            data["Administrations"].append({
                "intervention": si["name"], "name": a["name"], "label": a.get("label"), "description": a.get("description"),
                "dose_value": dose.get("value"), "dose_unit": _dec(dose.get("unit")), "route": _dec(a.get("route")),
                "frequency": _dec(a.get("frequency")),
                "product": nm(a["administrableProductId"]) if a.get("administrableProductId") else None,
                "duration_text": (a.get("duration") or {}).get("text")})
    data["Objectives"], data["Endpoints"] = [], []
    for o in d.get("objectives", []):
        data["Objectives"].append({"name": o["name"], "label": o.get("label"), "level": _dec(o["level"]), "text": o["text"],
                                   "description": o.get("description")})
        for e in o.get("endpoints", []):
            data["Endpoints"].append({"objective": o["name"], "name": e["name"], "label": e.get("label"),
                                      "level": _dec(e["level"]), "purpose": e.get("purpose"), "text": e["text"],
                                      "description": e.get("description")})
    data["Eligibility"] = [{"identifier": c["identifier"], "category": _dec(c["category"]), "name": c["name"],
                            "label": c.get("label"), "text": ids[c["criterionItemId"]]["text"]}
                           for c in d.get("eligibilityCriteria", [])]
    data["AnalysisPopulations"] = [{"name": a["name"], "label": a.get("label"), "text": a["text"]}
                                   for a in d.get("analysisPopulations", [])]
    data["Encounters"] = [{"name": e["name"], "label": e.get("label"), "description": e.get("description"),
                           "type": _dec(e["type"]), "setting": _decs(e.get("environmentalSettings")),
                           "contact_mode": _decs(e.get("contactModes"))} for e in d.get("encounters", [])]
    bc_cat = {m: c["name"] for c in v.get("bcCategories", []) for m in c.get("memberIds", [])}
    data["BCCategories"] = [{"name": c["name"], "label": c.get("label"), "description": c.get("description")}
                            for c in v.get("bcCategories", [])]
    data["BiomedicalConcepts"] = [{"name": b["name"], "label": b.get("label"), "ncit_code": b["code"]["standardCode"]["code"],
                                   "reference": b.get("reference"), "synonyms": "; ".join(b.get("synonyms", [])) or None,
                                   "category": bc_cat.get(b["id"]),
                                   **_prov(b, ("source", "method", "confidence", "evidence", "review_status"))}
                                  for b in v.get("biomedicalConcepts", [])]
    data["BCSurrogates"] = [{"name": x["name"], "label": x.get("label"), "description": x.get("description"),
                             "reference": x.get("reference"), **_prov(x, ("evidence", "review_status"))}
                            for x in v.get("bcSurrogates", [])]
    data["Activities"] = [{"name": a["name"], "label": a.get("label"), "description": a.get("description"),
                           "bc_category": "; ".join(nm(x) for x in a.get("bcCategoryIds", [])) or None,
                           "biomedical_concepts": "; ".join(nm(x) for x in a.get("biomedicalConceptIds", [])) or None,
                           "surrogates": "; ".join(nm(x) for x in a.get("bcSurrogateIds", [])) or None,
                           **_prov(a, ("source_text", "method", "confidence", "review_status"))}
                          for a in d.get("activities", [])]
    data["Conditions"] = [{"name": c["name"], "label": c.get("label"), "text": c["text"], "marker": None,
                           "applies_to": "; ".join(nm(x) for x in c.get("appliesToIds", []) if ids.get(x, {}).get("instanceType") == "Activity") or None}
                          for c in v.get("conditions", [])]
    data["Abbreviations"] = [{"abbreviation": a["abbreviatedText"], "expansion": a["expandedText"]} for a in v.get("abbreviations", [])]
    data["Narrative"] = [{"section_number": nc.get("sectionNumber"), "section_title": nc.get("sectionTitle"),
                          "text": ids.get(nc.get("contentItemId"), {}).get("text")} for nc in dv.get("contents", [])]
    act_order = [a["name"] for a in d.get("activities", [])]
    data["Timelines"], data["SoA"] = [], []
    for n, tl in enumerate(d.get("scheduleTimelines", []), 1):
        sheet = f"SoA_{n}"
        dur = tl.get("plannedDuration") or {}
        trig = next((a["name"] for a in d.get("activities", []) if a.get("timelineId") == tl["id"]), None)
        data["Timelines"].append({"name": tl["name"], "label": tl.get("label"), "description": tl.get("description"),
                                  "main": tl.get("mainTimeline"), "entry_condition": tl.get("entryCondition"), "sheet": sheet,
                                  "planned_duration_text": dur.get("text"),
                                  "planned_duration_value": (dur.get("quantity") or {}).get("value"),
                                  "planned_duration_unit": _dec((dur.get("quantity") or {}).get("unit")),
                                  "triggered_by_activity": trig})
        timing_of = {t["relativeFromScheduledInstanceId"]: t for t in tl.get("timings", [])}
        cols, used = [], set()
        insts = [i for i in tl.get("instances", []) if i.get("instanceType") == "ScheduledActivityInstance"]
        for i in insts:
            t = timing_of.get(i["id"])
            day = None
            if t:
                sign = -1 if (t.get("type") or {}).get("code") == "C201357" else 1
                val = t.get("value") or ""
                tm = re.match(r"^PT(\d+(?:\.\d+)?)([HM])$", val)
                if tm:
                    q = float(tm.group(1)) * sign
                    day = f"{q:g}{'h' if tm.group(2) == 'H' else 'm'}"
                else:
                    off = (_days(val) or 0) * sign
                    day = off + 1 if off >= 0 else off
            cols.append({"epoch": nm(i["epochId"]) if i.get("epochId") else None,
                         "encounter": nm(i["encounterId"]) if i.get("encounterId") else None,
                         "day": day, "window_lower": _days(t.get("windowLower")) if t else None,
                         "window_upper": _days(t.get("windowUpper")) if t else None, "label": i.get("label")})
            used |= set(i.get("activityIds", []))
        rows = []
        for a in d.get("activities", []):
            if a["id"] not in used:
                continue
            rows.append({"activity": a["name"], "cells": ["X" if a["id"] in i.get("activityIds", []) else None for i in insts]})
        rows.sort(key=lambda r: act_order.index(r["activity"]))
        data["SoA"].append({"sheet": sheet, "timeline": tl["name"], "description": tl.get("description"), "columns": cols, "rows": rows})
    return data
