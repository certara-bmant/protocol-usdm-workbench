"""Controlled terminology lookup.

Each CT 'field' maps human-friendly decodes (as typed in the Excel template) to C-codes.
Rows can be overridden / extended from the workbook's `Codes` sheet, so the CT is editable
without touching code.  All codes here were taken from Certara's CTX101-001 USDM v4 example or
checked against NCI EVS (see VERIFIED in tools/verify_ct.py); keep them that way.
"""
from __future__ import annotations

import re

CDISC = "http://www.cdisc.org"
DEFAULT_CT_VERSION = "2025-09-26"

# field -> list of (code, decode, [synonyms])
CT: dict[str, list[tuple[str, str, list[str]]]] = {
    "study_type": [("C98388", "Interventional Study", ["interventional"]),
                   ("C16084", "Observational Study", ["observational"])],
    "phase": [("C54721", "Early Phase 1 Trial", ["phase 0", "0", "early phase 1"]),
              ("C15600", "Phase I Trial", ["phase 1", "phase i", "1"]),
              ("C15693", "Phase I/II Trial", ["phase 1/2", "phase i/ii", "1/2", "phase 1-2"]),
              ("C15601", "Phase II Trial", ["phase 2", "phase ii", "2"]),
              ("C49686", "Phase IIa Trial", ["phase 2a", "phase iia", "2a"]),
              ("C15694", "Phase II/III Trial", ["phase 2/3", "phase ii/iii", "2/3"]),
              ("C15602", "Phase III Trial", ["phase 3", "phase iii", "3"]),
              ("C15603", "Phase IV Trial", ["phase 4", "phase iv", "4"])],
    "blinding": [("C49659", "Open Label Study", ["open label", "open-label", "open"]),
                 ("C28233", "Single Blind Study", ["single blind", "single-blind"]),
                 ("C15228", "Double Blind Study", ["double blind", "double-blind", "observer-blind", "observer blind"])],
    "model": [("C82639", "Parallel Study", ["parallel"]),
              ("C82637", "Crossover Study", ["crossover", "cross-over"]),
              ("C82640", "Single Group Study", ["single group"]),
              ("C142568", "Group Sequential Design", ["group sequential", "sequential cohort", "dose escalation"])],
    "characteristic": [("C46079", "Randomized Controlled Clinical Trial", ["randomized", "randomised"]),
                       ("C217004", "Single-Center Study", ["single center", "single-centre"]),
                       ("C217006", "Single Country", ["single country"])],
    "sub_type": [("C49667", "Safety Study", ["safety"]),
                 ("C98791", "Tolerability Study", ["tolerability"]),
                 ("C49663", "Pharmacokinetic Study", ["pk", "pharmacokinetic"]),
                 ("C120842", "Immunogenicity Study", ["immunogenicity"]),
                 ("C127803", "Dose Response Study", ["dose finding", "dose-finding", "dose response"])],
    "arm_type": [("C174266", "Investigational Arm", ["experimental", "investigational"]),
                 ("C174267", "Active Comparator Arm", ["active comparator", "active control"]),
                 ("C174268", "Placebo Control Arm", ["placebo"])],
    "data_origin_type": [("C188866", "Data Generated Within Study", ["within study", "collected"])],
    "epoch_type": [("C202487", "Screening Epoch", ["screening"]),
                   ("C101526", "Treatment Epoch", ["treatment", "dosing", "intervention"]),
                   ("C202578", "Follow-Up Epoch", ["follow-up", "follow up", "followup"])],
    "encounter_type": [("C25716", "Visit", ["visit", "phone call", "telephone"])],
    "environmental_setting": [("C211570", "Clinic", ["clinic", "site", "inpatient", "outpatient"])],
    "contact_mode": [("C175574", "In Person", ["in person", "on site"]),
                     ("C171537", "Telephone Call", ["telephone", "phone", "phone call"])],
    "intervention_role": [("C41161", "Protocol Agent", ["investigational", "investigational product", "study drug", "experimental"]),
                          ("C68609", "Active Comparator", ["comparator", "active comparator"]),
                          ("C753", "Placebo", ["placebo"])],
    "intervention_type": [("C1909", "Pharmacologic Substance", ["drug", "small molecule"]),
                          ("C923", "Vaccine", ["vaccine", "biological vaccine"]),
                          ("C307", "Biological Agent", ["biological", "biologic", "monoclonal antibody", "mab"])],
    "route": [("C38288", "Oral Route of Administration", ["oral", "po"]),
              ("C28161", "Intramuscular Route of Administration", ["intramuscular", "im"]),
              ("C38276", "Intravenous Route of Administration", ["intravenous", "iv", "iv infusion"])],
    "frequency": [("C64576", "Once", ["once", "single dose", "single"])],
    "dose_form": [("C42998", "Tablet Dosage Form", ["tablet"]),
                  ("C42995", "Injectable Suspension Dosage Form", ["suspension for injection", "injectable suspension", "suspension"]),
                  ("C42945", "Injectable Solution Dosage Form", ["solution for injection", "injectable solution", "solution"])],
    "product_designation": [("C202579", "Investigational Medicinal Product", ["imp", "investigational"])],
    "sourcing": [("C215659", "Centrally Sourced", ["central", "centrally sourced", "Centrally Sourced Indicator"]),
                 ("C215660", "Locally Sourced", ["local", "locally sourced"])],
    "unit": [("C25301", "Day", ["day", "days", "d"]),
             ("C25529", "Hour", ["hour", "hours", "h"]),
             ("C29848", "Year", ["year", "years", "y"]),
             ("C28253", "Milligram", ["mg"]),
             ("C48152", "Microgram", ["ug", "µg", "μg", "mcg"]),
             ("C28254", "Milliliter", ["ml", "mL"])],
    "objective_level": [("C85826", "Study Primary Objective", ["primary", "Trial Primary Objective"]),
                        ("C85827", "Study Secondary Objective", ["secondary", "Trial Secondary Objective"]),
                        ("C163559", "Exploratory Objective", ["exploratory"])],
    "endpoint_level": [("C94496", "Primary Endpoint", ["primary"]),
                       ("C139173", "Secondary Endpoint", ["secondary"]),
                       ("C170559", "Exploratory Endpoint", ["exploratory"])],
    "criterion_category": [("C25532", "Inclusion Criteria", ["inclusion", "i"]),
                           ("C25370", "Exclusion Criteria", ["exclusion", "e"])],
    "organization_type": [("C54149", "Drug Company", ["sponsor", "pharmaceutical company", "pharma"]),
                          ("C93453", "Clinical Study Registry", ["registry", "Study Registry"]),
                          ("C54148", "Contract Research Organization", ["cro"]),
                          ("C18240", "Academic Institution", ["academic"]),
                          ("C199144", "Government Institute", ["government", "regulator", "regulatory agency"])],
    "title_type": [("C207616", "Official Study Title", ["official"]),
                   ("C207615", "Brief Study Title", ["brief", "short"]),
                   ("C207646", "Study Acronym", ["acronym"])],
    "governance_date_type": [("C71476", "Approval Date", ["approval", "sponsor approval"]),
                             ("C215663", "Effective Date", ["effective"]),
                             ("C215664", "Issued Date", ["issued", "issue"])],
    "geographic_scope": [("C68846", "Global", ["global"]),
                         ("C41129", "Region", ["region"]),
                         ("C25464", "Country", ["country"])],
    "sex": [("C16576", "Female", ["f", "female", "women"]),
            ("C20197", "Male", ["m", "male", "men"])],
    "timing_type": [("C201358", "Fixed Reference Timing Type", ["fixed", "anchor", "fixed reference"]),
                    ("C201357", "Before Timing Type", ["before"]),
                    ("C201356", "After Timing Type", ["after"])],
    "timing_relative": [("C201355", "Start to Start", ["start to start", "s2s"])],
    "study_role": [("C70793", "Clinical Study Sponsor", ["sponsor"])],
    "document_type": [("C70817", "Study Protocol", ["protocol"])],
    "document_status": [("C25508", "Final", ["final"]),
                        ("C85255", "Draft", ["draft"])],
}

# Fields whose USDM attribute is an AliasCode (standardCode wrapper) rather than a bare Code
ALIAS_FIELDS = {"phase", "blinding", "route", "frequency", "dose_form", "unit"}


class CTError(ValueError):
    pass


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]+", " ", str(s).strip().lower())


class Terminology:
    def __init__(self, version: str = DEFAULT_CT_VERSION):
        self.version = version
        self.table: dict[str, dict[str, tuple[str, str, str, str]]] = {}
        for field, rows in CT.items():
            for code, decode, syns in rows:
                self.add(field, code, decode, syns)

    def add(self, field, code, decode, synonyms=(), system=CDISC, version=None):
        t = self.table.setdefault(field, {})
        entry = (code, decode, system, version or self.version)
        for key in [code, decode, *synonyms]:
            if key:
                t[_norm(key)] = entry

    def load_sheet(self, rows):
        """rows from the `Codes` sheet: field, decode, code, code_system, code_system_version, synonyms"""
        for r in rows:
            if not r.get("field") or not r.get("code"):
                continue
            syns = [s.strip() for s in str(r.get("synonyms") or "").split(";") if s.strip()]
            self.add(r["field"], str(r["code"]).strip(), str(r.get("decode") or r["code"]).strip(), syns,
                     r.get("code_system") or CDISC, r.get("code_system_version") or None)

    def lookup(self, field: str, value) -> tuple[str, str, str, str]:
        if value is None or str(value).strip() == "":
            raise CTError(f"empty value for {field}")
        t = self.table.get(field)
        if t is None:
            raise CTError(f"unknown CT field '{field}'")
        hit = t.get(_norm(value))
        if hit:
            return hit
        raise CTError(f"'{value}' is not a known term for {field}; add it to the Codes sheet "
                      f"(known: {sorted({v[1] for v in t.values()})})")

    def decodes(self, field):
        return sorted({v[1] for v in self.table.get(field, {}).values()})

    def reverse(self, code: str):
        """code -> (field, decode) best effort (used by the JSON -> Excel exporter)."""
        out = []
        for field, t in self.table.items():
            for e in t.values():
                if e[0] == code:
                    out.append((field, e[1]))
        return out
