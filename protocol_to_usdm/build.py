"""StudyData (from the Excel template) -> USDM v4 JSON."""
from __future__ import annotations

import re

from . import __version__
from .bc_library import BCLibrary
from .ct import Terminology
from .model import Factory, chain, num, split, yes

EXT = "https://protocol-to-usdm.local/extensions/provenance"  # change to your organisation's namespace


class BuildError(ValueError):
    pass


class Builder:
    def __init__(self, data: dict, library: BCLibrary | None = None, ct: Terminology | None = None):
        self.d = data
        self.ct = ct or Terminology()
        self.ct.load_sheet(data.get("Codes", []))
        self.f = Factory(self.ct)
        self.lib = library or BCLibrary.default()
        self.ids: dict[str, dict[str, str]] = {}  # sheet -> name -> id
        self.warnings: list[str] = []

    # --------------------------------------------------------------------------------- helpers
    def reg(self, kind, name, obj):
        if name in self.ids.setdefault(kind, {}):
            raise BuildError(f"duplicate {kind} name '{name}'")
        self.ids[kind][name] = obj["id"]
        return obj

    def ref(self, kind, name, where=""):
        try:
            return self.ids[kind][name]
        except KeyError:
            raise BuildError(f"{where}: unknown {kind} '{name}' (known: {sorted(self.ids.get(kind, {}))})") from None

    def refs(self, kind, value, where=""):
        return [self.ref(kind, n, where) for n in split(value)]

    def req(self, value, where):
        """USDM-mandatory text that the source did not provide: keep the file valid but make the gap visible."""
        if value in (None, ""):
            self.warnings.append(f"{where} is mandatory in USDM but empty - filled with '[not provided]'")
            return "[not provided]"
        return value

    def rows(self, sheet):
        return self.d.get(sheet, []) or []

    def prov(self, row, *cols):
        out = []
        for c in cols:
            if row.get(c) not in (None, ""):
                out.append(self.f.ext(f"{EXT}#{c}", row[c]))
        return out

    def _age_range(self, r):
        if r.get("min_age") in (None, "") or r.get("max_age") in (None, ""):
            return None
        unit = r.get("age_unit") or "Year"
        return self.f.make("Range", minValue=self.f.quantity(r["min_age"], unit),
                           maxValue=self.f.quantity(r["max_age"], unit), isApproximate=False)

    # --------------------------------------------------------------------------------- build
    def build(self) -> dict:
        f, S = self.f, self.d.get("Study", {})
        if not S.get("name"):
            raise BuildError("Study.name is required")

        orgs = []
        for r in self.rows("Organizations"):
            orgs.append(self.reg("org", r["name"], f.make(
                "Organization", name=r["name"], label=r.get("label"), type=f.code("organization_type", r.get("type")),
                identifierScheme=r.get("identifier_scheme") or "N/A", identifier=str(r.get("identifier") or r["name"]))))
        idents = [f.make("StudyIdentifier", text=str(r["text"]), scopeId=self.ref("org", r["organization"], "Identifiers"))
                  for r in self.rows("Identifiers")]
        titles = [f.make("StudyTitle", text=r["text"], type=f.code("title_type", r["type"])) for r in self.rows("Titles")]
        dates = []
        if S.get("approval_date"):
            dates.append(f.make("GovernanceDate", name="SPONSOR_APPROVAL", label="Sponsor approval",
                                type=f.code("governance_date_type", "Approval Date"), dateValue=str(S["approval_date"])[:10],
                                geographicScopes=[f.make("GeographicScope", type=f.code("geographic_scope", "Global"))]))

        # products / interventions
        products = []
        for r in self.rows("Products"):
            products.append(self.reg("product", r["name"], f.make(
                "AdministrableProduct", name=r["name"], label=r.get("label"), description=r.get("description"),
                administrableDoseForm=f.alias("dose_form", r.get("dose_form")),
                productDesignation=f.code("product_designation", r.get("designation") or "Investigational Medicinal Product"),
                sourcing=f.coded("sourcing", r.get("sourcing")))))
        admins: dict[str, list] = {}
        for r in self.rows("Administrations"):
            admins.setdefault(r["intervention"], []).append(f.make(
                "Administration", name=r["name"], label=r.get("label"), description=r.get("description"),
                dose=f.quantity(r.get("dose_value"), r.get("dose_unit")), route=f.coded("route", r.get("route")),
                frequency=f.coded("frequency", r.get("frequency")),
                duration=f.make("Duration", text=r.get("duration_text"), quantity=None, durationWillVary=False),
                administrableProductId=self.ref("product", r["product"], "Administrations") if r.get("product") else None))
        interventions = []
        for r in self.rows("Interventions"):
            interventions.append(self.reg("intervention", r["name"], f.make(
                "StudyIntervention", name=r["name"], label=r.get("label"), description=r.get("description"),
                role=f.code("intervention_role", r.get("role")), type=f.code("intervention_type", r.get("type")),
                administrations=admins.pop(r["name"], []))))
        for k in admins:
            raise BuildError(f"Administrations: unknown intervention '{k}'")

        # eligibility
        items, crits = [], []
        for r in self.rows("Eligibility"):
            name = r.get("name") or f"{'INC' if str(r['category']).lower().startswith('i') else 'EXC'}{r['identifier']}"
            it = f.make("EligibilityCriterionItem", name=name, label=r.get("label"), text=r["text"])
            items.append(it)
            crits.append(self.reg("criterion", str(r["identifier"]) + ":" + str(r["category"]), f.make(
                "EligibilityCriterion", name=name, label=r.get("label"), identifier=str(r["identifier"]),
                category=f.code("criterion_category", r["category"]), criterionItemId=it["id"])))
        chain(crits)
        if not crits:
            self.warnings.append("no eligibility criteria (USDM requires 1..*)")

        # population / cohorts
        cohorts = []
        for r in self.rows("Cohorts"):
            cohorts.append(self.reg("cohort", r["name"], f.make(
                "StudyCohort", name=r["name"], label=r.get("label"), description=r.get("description"),
                includesHealthySubjects=yes(r.get("healthy")), plannedEnrollmentNumber=f.quantity(r.get("planned_enrollment")),
                plannedSex=f.codes("sex", r.get("sex")), plannedAge=self._age_range(r))))
        P = self.d.get("Population", {}) or {}
        population = f.make("StudyDesignPopulation", name=P.get("name") or "POP", label=P.get("label"),
                            description=P.get("description"), includesHealthySubjects=yes(P.get("healthy")),
                            plannedEnrollmentNumber=f.quantity(P.get("planned_enrollment")),
                            plannedCompletionNumber=f.quantity(P.get("planned_completion")),
                            plannedSex=f.codes("sex", P.get("sex")), plannedAge=self._age_range(P),
                            criterionIds=[c["id"] for c in crits], cohorts=cohorts)
        self.ids.setdefault("cohort", {})[population["name"]] = population["id"]

        # design structure
        epochs = chain([self.reg("epoch", r["name"], f.make(
            "StudyEpoch", name=r["name"], label=r.get("label"), description=r.get("description"),
            type=f.code("epoch_type", r["type"]))) for r in self.rows("Epochs")])
        arms = [self.reg("arm", r["name"], f.make(
            "StudyArm", name=r["name"], label=r.get("label"), description=r.get("description"),
            type=f.code("arm_type", r["type"]),
            dataOriginType=f.code("data_origin_type", r.get("data_origin_type") or "Data Generated Within Study"),
            dataOriginDescription=r.get("data_origin_description") or "Data collected in study",
            populationIds=self.refs("cohort", r.get("cohorts"), f"Arms[{r['name']}]"))) for r in self.rows("Arms")]
        elements = [self.reg("element", r["name"], f.make(
            "StudyElement", name=r["name"], label=r.get("label"), description=r.get("description"),
            studyInterventionIds=self.refs("intervention", r.get("interventions"), f"Elements[{r['name']}]"),
            transitionStartRule=f.rule(f"{r['name']}_START", r.get("start_rule")),
            transitionEndRule=f.rule(f"{r['name']}_END", r.get("end_rule")))) for r in self.rows("Elements")]
        cells = [f.make("StudyCell", armId=self.ref("arm", r["arm"], "Cells"), epochId=self.ref("epoch", r["epoch"], "Cells"),
                        elementIds=self.refs("element", r.get("elements"), "Cells")) for r in self.rows("Cells")]

        # biomedical concepts
        bcs, cats, surrogates = self._bcs()

        # activities
        acts = []
        for r in self.rows("Activities"):
            acts.append(self.reg("activity", r["name"], f.make(
                "Activity", name=r["name"], label=r.get("label") or r["name"], description=r.get("description"),
                biomedicalConceptIds=self.refs("bc", r.get("biomedical_concepts"), f"Activities[{r['name']}]"),
                bcSurrogateIds=self.refs("surrogate", r.get("surrogates"), f"Activities[{r['name']}]"),
                bcCategoryIds=self.refs("bccat", r.get("bc_category"), f"Activities[{r['name']}]"),
                extensionAttributes=self.prov(r, "source_text", "method", "confidence", "review_status"))))
        chain(acts)
        self._acts_cache = acts

        encounters = chain([self.reg("encounter", r["name"], f.make(
            "Encounter", name=r["name"], label=r.get("label"), description=r.get("description"),
            type=f.code("encounter_type", r.get("type") or "Visit"),
            environmentalSettings=f.codes("environmental_setting", r.get("setting")),
            contactModes=f.codes("contact_mode", r.get("contact_mode")))) for r in self.rows("Encounters")])

        conditions = [self.reg("condition", r["name"], f.make(
            "Condition", name=r["name"], label=r.get("label"), text=r["text"],
            appliesToIds=self.refs("activity", r.get("applies_to"), "Conditions"),
            contextIds=[])) for r in self.rows("Conditions")]
        markers = {(r.get("timeline"), str(r["marker"]).strip()): c
                   for r, c in zip(self.rows("Conditions"), conditions) if r.get("marker")}

        timelines = self._timelines(encounters, markers)

        objectives = []
        eps: dict[str, list] = {}
        for r in self.rows("Endpoints"):
            eps.setdefault(r["objective"], []).append(f.make(
                "Endpoint", name=r["name"], label=r.get("label"), description=r.get("description"), text=r["text"],
                purpose=self.req(r.get("purpose"), f"Endpoints[{r['name']}].purpose"),
                level=f.code("endpoint_level", r["level"])))
        for r in self.rows("Objectives"):
            objectives.append(f.make("Objective", name=r["name"], label=r.get("label"), description=r.get("description"), text=r["text"],
                                     level=f.code("objective_level", r["level"]), endpoints=eps.pop(r["name"], [])))
        for k in eps:
            raise BuildError(f"Endpoints: unknown objective '{k}'")

        analysis = [f.make("AnalysisPopulation", name=r["name"], label=r.get("label"), text=r["text"])
                    for r in self.rows("AnalysisPopulations")]
        abbrevs = [f.make("Abbreviation", abbreviatedText=r["abbreviation"], expandedText=r["expansion"])
                   for r in self.rows("Abbreviations")]

        # document + narrative
        n_items, contents = [], []
        for i, r in enumerate(self.rows("Narrative"), 1):
            it = f.make("NarrativeContentItem", name=f"NCI_{i}", text=r.get("text") or "")
            n_items.append(it)
            contents.append(f.make("NarrativeContent", name=f"NC_{i}", sectionNumber=str(r.get("section_number") or ""),
                                   sectionTitle=r.get("section_title"), displaySectionNumber=True,
                                   displaySectionTitle=True, contentItemId=it["id"]))
        chain(contents)
        doc_ver = f.make("StudyDefinitionDocumentVersion", version=str(S.get("document_version") or S.get("version") or "1"),
                         status=f.code("document_status", S.get("document_status") or "Final"), contents=contents)
        doc = f.make("StudyDefinitionDocument", name=S.get("document_name") or f"{S['name']} Protocol", label="Protocol",
                     language=f.raw_code(S.get("language") or "en", "English" if (S.get("language") or "en") == "en" else S.get("language"),
                                         "ISO 639-1", "2007"),
                     type=f.code("document_type", "Study Protocol"), templateName=S.get("document_template") or "Sponsor",
                     versions=[doc_ver])

        roles = []
        if S.get("sponsor"):
            roles.append(f.make("StudyRole", name="SPONSOR", label="Sponsor", code=f.code("study_role", "Clinical Study Sponsor"),
                                organizationIds=[self.ref("org", S["sponsor"], "Study.sponsor")]))

        design = f.make(
            "InterventionalStudyDesign", name=S.get("design_name") or f"{S['name']} Design", label=S.get("design_name"),
            description=S.get("design_description"), rationale=self.req(S.get("design_rationale"), "Study.design_rationale"),
            studyType=f.coded("study_type", S.get("study_type") or "Interventional Study"),
            studyPhase=f.coded("phase", S.get("phase")), blindingSchema=f.coded("blinding", S.get("blinding")),
            model=f.code("model", S.get("model") or "Parallel Study"),
            characteristics=f.codes("characteristic", S.get("characteristics")),
            subTypes=f.codes("sub_type", S.get("sub_types")),
            activities=acts, encounters=encounters, arms=arms, studyCells=cells, epochs=epochs, elements=elements,
            objectives=objectives, population=population, scheduleTimelines=timelines,
            studyInterventionIds=[i["id"] for i in interventions], eligibilityCriteria=crits,
            analysisPopulations=analysis, documentVersionIds=[doc_ver["id"]])

        tas = [f.raw_code(re.sub(r"\W+", "-", t.upper())[:20], t, "sponsor-therapeutic-areas", "1")
               for t in split(S.get("therapeutic_areas"))]
        version = f.make(
            "StudyVersion", versionIdentifier=str(S.get("version") or "1"), rationale=S.get("version_rationale") or "Initial",
            documentVersionIds=[doc_ver["id"]], dateValues=dates, businessTherapeuticAreas=tas, studyIdentifiers=idents,
            studyDesigns=[design], titles=titles, eligibilityCriterionItems=items, narrativeContentItems=n_items,
            abbreviations=abbrevs, roles=roles, organizations=orgs, studyInterventions=interventions,
            administrableProducts=products, biomedicalConcepts=bcs, bcCategories=cats, bcSurrogates=surrogates,
            conditions=conditions)
        study = f.make("Study", name=S["name"], label=S.get("label"), description=S.get("description"),
                       versions=[version], documentedBy=[doc])
        return {"study": study, "usdmVersion": "4.0.0", "systemName": "protocol_to_usdm", "systemVersion": __version__}

    # --------------------------------------------------------------------------------- BCs
    def _bcs(self):
        f = self.f
        cats_rows = {r["name"]: r for r in self.rows("BCCategories")}
        bcs, members = [], {}
        for r in self.rows("BiomedicalConcepts"):
            entry = self.lib.by_code(str(r["ncit_code"]), r["name"]) if r.get("ncit_code") else self.lib.by_name(r["name"])
            code = str(r.get("ncit_code") or (entry.ncit_code if entry else ""))
            if not code:
                raise BuildError(f"BiomedicalConcepts[{r['name']}]: ncit_code required (or use BCSurrogates)")
            props = BCLibrary.instantiate_properties(entry, f) if entry and entry.properties else []
            if not props:
                self.warnings.append(f"BC '{r['name']}' ({code}) has no CDISC BC properties - review / request BC")
            ext = [f.ext(e["url"], e.get("valueString")) for e in (entry.extensions if entry else [])]
            ext += self.prov(r, "source", "method", "confidence", "evidence", "review_status")
            bc = f.make("BiomedicalConcept", name=r["name"], label=r.get("label") or r["name"],
                        synonyms=split(r.get("synonyms")) or (entry.synonyms if entry else []),
                        reference=r.get("reference") or (entry.reference if entry else f"/mdr/bc/biomedicalconcepts/{code}"),
                        code=f.make("AliasCode", standardCode=f.raw_code(code, (entry.decode if entry else None) or r["name"],
                                                                          "http://www.cdisc.org", self.ct.version)),
                        properties=props, extensionAttributes=ext)
            bcs.append(self.reg("bc", r["name"], bc))
            if r.get("category"):
                members.setdefault(r["category"], []).append(bc["id"])
                cats_rows.setdefault(r["category"], {"name": r["category"]})
        cats = []
        for name, r in cats_rows.items():
            cats.append(self.reg("bccat", name, f.make("BiomedicalConceptCategory", name=name, label=r.get("label") or name,
                                                       description=r.get("description"), memberIds=members.get(name, []))))
        surrogates = [self.reg("surrogate", r["name"], f.make(
            "BiomedicalConceptSurrogate", name=r["name"], label=r.get("label") or r["name"], description=r.get("description"),
            reference=r.get("reference") or "Sponsor-defined",
            extensionAttributes=self.prov(r, "evidence", "review_status"))) for r in self.rows("BCSurrogates")]
        return bcs, cats, surrogates

    # --------------------------------------------------------------------------------- SoA
    @staticmethod
    def _point(v):
        """SoA 'Day' cell -> (minutes relative to Day 1 00:00 or to clock 0, label, is_anchor) or None."""
        if v in (None, ""):
            return None
        s = str(v).strip().lower().replace(" ", "")
        m = re.match(r"^([+-]?\d+(?:\.\d+)?)(h|hr|hours?|m|min|mins?)$", s)
        if m:
            q = float(m.group(1))
            mins = q * 60 if m.group(2).startswith("h") else q
            q_txt = f"{q:g} {'h' if m.group(2).startswith('h') else 'min'}"
            return (mins, q_txt, mins == 0)
        day = num(v)
        off = day - 1 if day > 0 else day  # there is no Day 0
        return (off * 1440, f"Day {day}", day == 1)

    @classmethod
    def _win(cls, v):
        """window cell -> (ISO duration, label); numbers are days, '5m' / '1h' are clock units."""
        if v in (None, ""):
            return None
        s = str(v).strip().lower().replace(" ", "")
        m = re.match(r"^(\d+(?:\.\d+)?)(h|hr|hours?|m|min|mins?|d|days?)?$", s)
        if not m:
            raise BuildError(f"window value '{v}' not understood (use days, or e.g. '5m', '1h')")
        q, u = float(m.group(1)), (m.group(2) or "d")[0]
        mins = q * {"d": 1440, "h": 60, "m": 1}[u]
        return cls._iso(mins), f"{q:g} {'days' if u == 'd' else ('h' if u == 'h' else 'min')}"

    @staticmethod
    def _iso(minutes, clock=False):
        minutes = int(round(minutes))
        if minutes % 1440 == 0 and not clock:
            return f"P{minutes // 1440}D"
        if minutes % 60 == 0:
            return f"PT{minutes // 60}H"
        return f"PT{minutes}M"

    def _timelines(self, encounters, markers):
        f = self.f
        meta = {r["name"]: r for r in self.rows("Timelines")}
        enc_obj = {e["name"]: e for e in encounters}
        out = []
        for tl in self.d.get("SoA", []) or []:
            name = tl["timeline"]
            m = meta.get(name, {})
            cols = tl["columns"]
            if not cols:
                continue
            instances, timings = [], []
            for col in cols:
                enc = col.get("encounter")
                epoch = col.get("epoch")
                acts = []
                for row in tl["rows"]:
                    idx = cols.index(col)
                    cell = row["cells"][idx] if idx < len(row["cells"]) else None
                    if not cell:
                        continue
                    aid = self.ref("activity", row["activity"], f"{tl['sheet']}")
                    acts.append(aid)
                    for mk in re.sub(r"(?i)^x", "", re.sub(r"\{.*?\}", "", cell.strip())).strip():
                        c = markers.get((name, mk)) or markers.get((None, mk))
                        if mk.strip() and c:
                            if aid not in c["appliesToIds"]:
                                c["appliesToIds"].append(aid)
                            col.setdefault("_markers", []).append(c)
                        elif mk.strip():
                            self.warnings.append(f"{tl['sheet']}: footnote marker '{mk}' has no Conditions row")
                iname = enc or col.get("label") or f"T{cols.index(col) + 1}"
                inst = f.make("ScheduledActivityInstance", name=f"{name}_{iname}", label=col.get("label") or enc,
                              description=f"{iname} ({name})", encounterId=self.ref("encounter", enc, tl["sheet"]) if enc else None,
                              epochId=self.ref("epoch", epoch, tl["sheet"]) if epoch else None, activityIds=acts)
                for mk in str(col.get("notes") or "").replace(";", " ").split():
                    for ch in mk:
                        c = markers.get((name, ch)) or markers.get((None, ch))
                        if c:
                            col.setdefault("_markers", []).append(c)
                        else:
                            self.warnings.append(f"{tl['sheet']}: column note marker '{ch}' has no Conditions row")
                for c in col.pop("_markers", []):
                    if inst["id"] not in c["contextIds"]:
                        c["contextIds"].append(inst["id"])
                instances.append(inst)
            exit_ = f.make("ScheduleTimelineExit")
            for a, b in zip(instances, instances[1:]):
                a["defaultConditionId"] = b["id"]
            instances[-1]["timelineExitId"] = exit_["id"]
            # timings: 'day' is a study day number (Day 1 = anchor) or a clock offset like '-1h', '0h', '30m'
            pts = [self._point(c.get("day")) for c in cols]
            anchor_i = next((i for i, p in enumerate(pts) if p and p[2]), None)
            if anchor_i is None:
                anchor_i = next((i for i, p in enumerate(pts) if p), None)
            if anchor_i is not None:
                a_min = pts[anchor_i][0]
                for i, (col, inst) in enumerate(zip(cols, instances)):
                    if not pts[i]:
                        continue
                    diff, label = pts[i][0] - a_min, pts[i][1]
                    typ = "Fixed Reference" if i == anchor_i else ("Before" if diff < 0 else "After")
                    lo, hi = self._win(col.get("window_lower")), self._win(col.get("window_upper"))
                    wl = None
                    if lo or hi:
                        wl = f"-{(lo or ('P0D', '0 days'))[1]} / +{(hi or ('P0D', '0 days'))[1]}"
                    t = f.make("Timing", name=f"TIM_{name}_{col.get('encounter') or col.get('label')}", label=label,
                               type=f.code("timing_type", typ), value=self._iso(abs(diff), not label.startswith("Day")), valueLabel=label,
                               relativeToFrom=f.code("timing_relative", "Start to Start"),
                               relativeFromScheduledInstanceId=inst["id"],
                               relativeToScheduledInstanceId=None if i == anchor_i else instances[anchor_i]["id"],
                               windowLower=lo[0] if lo else None, windowUpper=hi[0] if hi else None, windowLabel=wl)
                    timings.append(t)
                    e = enc_obj.get(col.get("encounter"))
                    if e is not None and (e["scheduledAtId"] is None or yes(m.get("main"))):
                        e["scheduledAtId"] = t["id"]
            dur = None
            if m.get("planned_duration_value") not in (None, ""):
                dur = f.make("Duration", text=m.get("planned_duration_text"),
                             quantity=f.quantity(m["planned_duration_value"], m.get("planned_duration_unit") or "Day"),
                             durationWillVary=False)
            st = f.make("ScheduleTimeline", name=name, label=m.get("label") or name,
                        description=m.get("description") or tl.get("description"), mainTimeline=yes(m.get("main")),
                        entryCondition=m.get("entry_condition") or "Informed consent signed", entryId=instances[0]["id"],
                        exits=[exit_], timings=timings, instances=instances, plannedDuration=dur)
            out.append(self.reg("timeline", name, st))
            if m.get("triggered_by_activity"):
                aid = self.ref("activity", m["triggered_by_activity"], "Timelines")
                for a in self._all_activities():
                    if a["id"] == aid:
                        a["timelineId"] = st["id"]
        if out and not any(t["mainTimeline"] for t in out):
            out[0]["mainTimeline"] = True
            self.warnings.append("no timeline flagged main=Y; first timeline set as main")
        return out

    def _all_activities(self):
        return getattr(self, "_acts_cache", [])


def build(data: dict, library: BCLibrary | None = None, ct: Terminology | None = None):
    b = Builder(data, library, ct)
    out = b.build()
    return out, b.warnings
