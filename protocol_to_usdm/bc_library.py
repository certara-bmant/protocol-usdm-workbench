"""Biomedical Concept library + tiered matcher.

Sources that can be loaded (any combination):
  * seed JSON harvested from an existing USDM file (tools/harvest_bcs.py) - full USDM property structure
  * CDISC COSMoS export CSV (github.com/cdisc-org/COSMoS export/cdisc_biomedical_concepts_latest.csv,
    MIT / CC-BY-4.0) - properties built from the DEC rows
  * NCI EVS cache / live EVS REST API - NCIt concept only (no properties) -> always flagged for review

Matching tiers (method, typical confidence):
  1. exact      - normalised name / synonym / curated synonym table hit           (1.00)
  2. fuzzy      - token + character similarity over names and synonyms              (0.60-0.95)
  3. evs        - NCIt concept found in EVS but no CDISC BC                          (0.65, review)
  4. surrogate  - nothing usable -> BiomedicalConceptSurrogate                       (review)
An optional `reranker(term, context, candidates) -> candidates` callable (e.g. an LLM) can reorder tier 2.
"""
from __future__ import annotations

import copy
import csv
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

DATA = Path(__file__).parent / "data"
STOP = {"in", "of", "the", "and", "or", "concentration", "presence", "count", "level", "levels", "test", "tests",
        "serum", "plasma", "blood", "urine", "total", "by", "format", "free", "text", "measurement", "sample", "for"}
SPECIMEN_WORDS = {"urine": "urine", "urinalysis": "urine", "serum": "serum or plasma", "plasma": "serum or plasma",
                  "blood": "blood"}


def norm(s: str) -> str:
    s = str(s).lower().replace("γ", "gamma ").replace("µ", "u").replace("μ", "u")
    s = re.sub(r"[^a-z0-9/+ ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tokens(s: str) -> set[str]:
    return {t for t in norm(s).replace("/", " ").split() if t not in STOP}


@dataclass
class Entry:
    ncit_code: str
    name: str
    synonyms: list[str] = field(default_factory=list)
    reference: str | None = None
    category: str | None = None
    source: str = ""
    properties: list = field(default_factory=list)
    extensions: list = field(default_factory=list)
    label: str | None = None
    decode: str | None = None
    kind: str = "bc"  # bc | surrogate | ncit


@dataclass
class Match:
    term: str
    entry: Entry | None
    score: float
    method: str
    alternatives: list = field(default_factory=list)
    issue: str | None = None

    @property
    def review(self) -> str:
        if self.entry is None or self.issue or self.method in ("surrogate", "evs") or self.score < 0.9:
            return "needs review"
        return "auto-accepted"


class BCLibrary:
    def __init__(self):
        self.entries: dict[str, Entry] = {}
        self.surrogates: dict[str, Entry] = {}
        self.index: dict[str, list[Entry]] = {}
        self.synonyms: dict[str, list[str]] = {}
        self.evs_cache: dict[str, dict] = {}
        self.evs_online = False
        self.issues: dict[str, str] = {}

    # ------------------------------------------------------------------ loading
    @classmethod
    def default(cls, evs_online: bool = False) -> "BCLibrary":
        lib = cls()
        lib.load_seed(DATA / "bc_library_seed.json")
        lib.load_synonyms(DATA / "synonyms.json")
        if (DATA / "seed_issues.json").exists():
            lib.issues = json.loads((DATA / "seed_issues.json").read_text(encoding="utf-8")).get("open", {})
        if (DATA / "evs_cache.json").exists():
            lib.evs_cache = json.loads((DATA / "evs_cache.json").read_text(encoding="utf-8"))
        lib.evs_online = evs_online
        return lib

    def _add(self, e: Entry):
        target = self.surrogates if e.kind == "surrogate" else self.entries
        key = e.name if e.kind == "surrogate" else e.ncit_code + "|" + e.name
        target[key] = e
        for k in [e.name, e.label, e.decode, *e.synonyms]:
            if k:
                self.index.setdefault(norm(k), []).append(e)

    def load_seed(self, path):
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        for c in d.get("concepts", []):
            self._add(Entry(c["ncit_code"], c["name"], c.get("synonyms", []), c.get("reference"), c.get("category"),
                            c.get("source", "seed"), c.get("properties", []), c.get("extensions", []),
                            c.get("label"), c.get("decode")))
        for s in d.get("surrogates", []):
            self._add(Entry("", s["name"], [], s.get("reference"), None, "seed surrogate", kind="surrogate",
                            label=s.get("description")))

    def load_cosmos_csv(self, path):
        """CDISC COSMoS export (one row per data element concept)."""
        rows: dict[str, list[dict]] = {}
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows.setdefault(r["bc_id"], []).append(r)
        n = 0
        for bc_id, rs in rows.items():
            r0 = rs[0]
            props = []
            for r in rs:
                if not r.get("dec_label"):
                    continue
                props.append({
                    "name": r["dec_label"], "label": r["dec_label"], "isRequired": True, "isEnabled": True,
                    "datatype": r.get("data_type") or "string",
                    "responseCodes": [],
                    "code": {"standardCode": {"code": r.get("ncit_dec_code") or r.get("dec_id"),
                                              "codeSystem": "http://www.cdisc.org", "codeSystemVersion": r.get("package_date"),
                                              "decode": r["dec_label"], "instanceType": "Code", "extensionAttributes": []},
                             "standardCodeAliases": [], "instanceType": "AliasCode", "extensionAttributes": []},
                    "notes": [], "instanceType": "BiomedicalConceptProperty", "extensionAttributes": []})
            syns = [s.strip() for s in (r0.get("synonyms") or "").split(";") if s.strip()]
            cats = [c.strip() for c in (r0.get("bc_categories") or "").split(";") if c.strip()]
            self._add(Entry(r0.get("ncit_code") or bc_id, r0["short_name"], syns,
                            f"/mdr/bc/biomedicalconcepts/{bc_id}", cats[0] if cats else None,
                            f"CDISC COSMoS {r0.get('package_date')}", props))
            n += 1
        return n

    def load_synonyms(self, path):
        if Path(path).exists():
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            self.synonyms = {norm(k): (v if isinstance(v, list) else [v]) for k, v in raw.items() if not k.startswith("_")}

    # ------------------------------------------------------------------ lookup
    def by_name(self, name: str) -> Entry | None:
        hits = self.index.get(norm(name), [])
        return hits[0] if hits else None

    def by_code(self, code: str, name: str | None = None) -> Entry | None:
        cands = [e for e in self.entries.values() if e.ncit_code == code]
        if name:
            for e in cands:
                if norm(e.name) == norm(name):
                    return e
        return cands[0] if cands else None

    def expand(self, term: str) -> list[str] | None:
        """Curated panel expansion, e.g. '12-lead ECG' -> its component BC names."""
        v = self.synonyms.get(norm(term))
        return v if v and len(v) > 1 else None

    def _specimen_ok(self, e: Entry, specimen: str | None) -> bool:
        if not specimen:
            return True
        n = norm(e.name)
        if specimen == "urine":
            return "urine" in n
        return "urine" not in n

    def match(self, term: str, specimen: str | None = None, context: str = "", reranker=None) -> Match:
        m = self._match(term, specimen, context, reranker)
        if m.entry is not None and m.entry.ncit_code in self.issues:
            m.issue = self.issues[m.entry.ncit_code]
        return m

    def _match(self, term: str, specimen: str | None = None, context: str = "", reranker=None) -> Match:
        t = norm(term)
        # tier 1: curated synonyms, then names / synonyms in library
        keys = ([f"{specimen} {t}"] if specimen else []) + [t]
        for k in keys:
            for target in self.synonyms.get(k, [])[:1]:
                e = self.by_name(target)
                if e and self._specimen_ok(e, specimen):
                    return Match(term, e, 1.0 if e.kind != "surrogate" else 0.8,
                                 "exact (curated synonym)" if e.kind != "surrogate" else "surrogate (library)")
        for e in self.index.get(t, []):
            if self._specimen_ok(e, specimen):
                return Match(term, e, 1.0 if e.kind != "surrogate" else 0.8,
                             "exact" if e.kind != "surrogate" else "surrogate (library)")
        # tier 2: fuzzy
        tt = tokens(term)
        scored = []
        for e in list(self.entries.values()) + list(self.surrogates.values()):
            if not self._specimen_ok(e, specimen):
                continue
            best = 0.0
            for cand in [e.name, *(e.synonyms or [])]:
                ct = tokens(cand)
                if not ct or not tt:
                    continue
                jac = len(tt & ct) / len(tt | ct)
                cover = len(tt & ct) / len(tt)
                seq = SequenceMatcher(None, " ".join(sorted(tt)), " ".join(sorted(ct))).ratio()
                best = max(best, 0.45 * jac + 0.35 * cover + 0.20 * seq)
            if best > 0:
                scored.append((best, e))
        scored.sort(key=lambda x: -x[0])
        if reranker and scored:
            scored = reranker(term, context, scored[:10])
        if scored and scored[0][0] >= 0.6:
            s, e = scored[0]
            method = "fuzzy" if e.kind != "surrogate" else "surrogate (library, fuzzy)"
            return Match(term, e, round(min(s, 0.95), 2), method, [x[1].name for x in scored[1:4]])
        # tier 3: NCI EVS (cache, then live if enabled)
        hit = self.evs_lookup(term)
        if not hit:
            w = term.split()
            for variant in ([" ".join(w[1:]), " ".join(w[:-1])] if len(w) > 1 else []):
                if len(variant) > 3:
                    hit = self.evs_lookup(variant)
                    if hit:
                        break
        if hit:
            e = Entry(hit["code"], hit["name"], [], f"http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#{hit['code']}",
                      None, f"NCI EVS {hit.get('version', '')}".strip(), kind="ncit")
            return Match(term, e, 0.65, "evs", [x[1].name for x in scored[:3]])
        return Match(term, None, 0.0, "surrogate", [x[1].name for x in scored[:3]])

    def search(self, term: str, specimen: str | None = None, k: int = 12) -> list[dict]:
        """Ranked candidates for an interactive reviewer (library BCs, surrogates, EVS cache/live)."""
        t, tt = norm(term), tokens(term)
        out = []
        seen = set()
        for e in list(self.entries.values()) + list(self.surrogates.values()):
            if not self._specimen_ok(e, specimen):
                continue
            best = 0.0
            for cand in [e.name, e.label or "", e.decode or "", e.ncit_code, *(e.synonyms or [])]:
                if not cand:
                    continue
                if norm(cand) == t:
                    best = 1.0
                    break
                if t and t in norm(cand):
                    best = max(best, 0.85)
                ct = tokens(cand)
                if ct and tt:
                    jac = len(tt & ct) / len(tt | ct)
                    cover = len(tt & ct) / len(tt)
                    seq = SequenceMatcher(None, " ".join(sorted(tt)), " ".join(sorted(ct))).ratio()
                    best = max(best, 0.45 * jac + 0.35 * cover + 0.20 * seq)
            if best >= 0.3 and (e.kind, e.name) not in seen:
                seen.add((e.kind, e.name))
                out.append({"name": e.name, "ncit_code": e.ncit_code, "reference": e.reference, "category": e.category,
                            "kind": e.kind, "source": e.source, "score": round(best, 2), "properties": len(e.properties),
                            "issue": self.issues.get(e.ncit_code)})
        hit = self.evs_lookup(term)
        if hit:
            out.append({"name": hit["name"], "ncit_code": hit["code"], "kind": "ncit", "score": 0.65, "properties": 0,
                        "reference": f"http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#{hit['code']}",
                        "source": f"NCI EVS {hit.get('version', '')}".strip(), "category": None, "issue": None})
        out.sort(key=lambda x: -x["score"])
        return out[:k]

    # ------------------------------------------------------------------ EVS
    def evs_lookup(self, term: str):
        key = norm(term)
        if key in self.evs_cache:
            return self.evs_cache[key]
        if not self.evs_online:
            return None
        hit = None
        for q in (term, f"{term} Measurement"):  # lab analytes are '<analyte> Measurement' in NCIt
            url = ("https://api-evsrest.nci.nih.gov/api/v1/concept/ncit/search?include=minimal&type=match&term="
                   + urllib.parse.quote(q))
            try:
                with urllib.request.urlopen(url, timeout=15) as r:
                    d = json.load(r)
            except Exception:
                return None
            hit = (d.get("concepts") or [None])[0]
            if hit:
                hit["query"] = f"{q} (match)"
                break
        self.evs_cache[key] = hit
        return hit

    @staticmethod
    def evs_verify(code: str) -> dict | None:
        """Confirm a C-code exists and is active (guards against invented codes)."""
        url = f"https://api-evsrest.nci.nih.gov/api/v1/concept/ncit/{code}?include=minimal"
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                d = json.load(r)
            return d if d.get("active") else None
        except Exception:
            return None

    # ------------------------------------------------------------------ instantiate for USDM
    @staticmethod
    def instantiate_properties(entry: Entry, factory) -> list:
        """Deep-copy library properties assigning fresh USDM ids."""
        def walk(o):
            if isinstance(o, dict):
                o = {k: walk(v) for k, v in o.items()}
                if "instanceType" in o:
                    o = {"id": factory.new_id(o["instanceType"]), **o}
                return o
            if isinstance(o, list):
                return [walk(x) for x in o]
            return o
        return [walk(copy.deepcopy(p)) for p in entry.properties]
