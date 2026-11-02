"""Build protocol_to_usdm/data/usdm_v4_model.json.

Sources (both permissively licensed / our own):
  * CDISC DDF-RA  Deliverables/UML/dataStructure.yml  (MIT, (c) CDISC) -> parsed to ds_partial.json
    (the copy fetched in the build environment was truncated after 'IntercurrentEvent', so classes
    alphabetically after that are supplied by MANUAL below)
  * MANUAL: hand-written from the USDM v4 specification and checked against the key sets observed
    in Certara's own CTX101-001 USDM v4 example.

Usage: python tools/build_model_spec.py <ds_partial.json> <out.json>
"""
import json
import sys

V, R = "V", "R"
S, B, CODE, AC = "string", "boolean", "Code", "AliasCode"

def a(card, typ, rel=V):
    return {"card": card, "types": typ.split("|"), "rel": rel}

COMMON = {"id": a("1", S), "extensionAttributes": a("0..*", "ExtensionAttribute"), "instanceType": a("1", S)}
NLD = {"name": a("1", S), "label": a("0..1", S), "description": a("0..1", S)}
NOTES = {"notes": a("0..*", "CommentAnnotation")}

MANUAL = {
    "Masking": {"text": a("1", S), "isMasked": a("1", B)},
    "MedicalDevice": {**NLD, "hardwareVersion": a("0..1", S), "softwareVersion": a("0..1", S),
                      "embeddedProductId": a("0..1", "AdministrableProduct", R), "sourcing": a("0..1", CODE),
                      "identifiers": a("0..*", "MedicalDeviceIdentifier"), **NOTES},
    "NarrativeContent": {"name": a("1", S), "sectionNumber": a("0..1", S), "sectionTitle": a("0..1", S),
                         "displaySectionNumber": a("1", B), "displaySectionTitle": a("1", B),
                         "childIds": a("0..*", "NarrativeContent", R), "previousId": a("0..1", "NarrativeContent", R),
                         "nextId": a("0..1", "NarrativeContent", R), "contentItemId": a("0..1", "NarrativeContentItem", R)},
    "NarrativeContentItem": {"name": a("1", S), "text": a("1", S)},
    "Objective": {**NLD, "text": a("1", S), "level": a("1", CODE), "endpoints": a("0..*", "Endpoint"),
                  "dictionaryId": a("0..1", "SyntaxTemplateDictionary", R), **NOTES},
    "Organization": {**NLD, "type": a("1", CODE), "identifierScheme": a("1", S), "identifier": a("1", S),
                     "legalAddress": a("0..1", "Address"), "managedSites": a("0..*", "StudySite")},
    "Procedure": {**NLD, "procedureType": a("1", S), "code": a("1", CODE),
                  "studyInterventionId": a("0..1", "StudyIntervention", R), **NOTES},
    "Quantity": {"value": a("1", "float"), "unit": a("0..1", AC)},
    "Range": {"minValue": a("1", "Quantity"), "maxValue": a("1", "Quantity"), "isApproximate": a("1", B)},
    "ResponseCode": {"name": a("1", S), "label": a("0..1", S), "isEnabled": a("1", B), "code": a("1", CODE)},
    "ScheduleTimeline": {**NLD, "mainTimeline": a("1", B), "entryCondition": a("1", S),
                         "entryId": a("1", "ScheduledActivityInstance|ScheduledDecisionInstance", R),
                         "exits": a("0..*", "ScheduleTimelineExit"), "timings": a("0..*", "Timing"),
                         "instances": a("0..*", "ScheduledActivityInstance|ScheduledDecisionInstance"),
                         "plannedDuration": a("0..1", "Duration")},
    "ScheduleTimelineExit": {},
    "ScheduledActivityInstance": {**NLD, "defaultConditionId": a("0..1", "ScheduledActivityInstance|ScheduledDecisionInstance", R),
                                  "epochId": a("0..1", "StudyEpoch", R), "timelineId": a("0..1", "ScheduleTimeline", R),
                                  "timelineExitId": a("0..1", "ScheduleTimelineExit", R),
                                  "activityIds": a("0..*", "Activity", R), "encounterId": a("0..1", "Encounter", R)},
    "StudyArm": {**NLD, "type": a("1", CODE), "dataOriginDescription": a("1", S), "dataOriginType": a("1", CODE),
                 "populationIds": a("0..*", "StudyDesignPopulation|StudyCohort", R), **NOTES},
    "StudyCell": {"armId": a("1", "StudyArm", R), "epochId": a("1", "StudyEpoch", R),
                  "elementIds": a("1..*", "StudyElement", R)},
    "StudyCohort": {**NLD, "includesHealthySubjects": a("1", B), "plannedEnrollmentNumber": a("0..1", "Quantity|Range"),
                    "plannedCompletionNumber": a("0..1", "Quantity|Range"), "plannedSex": a("0..2", CODE),
                    "plannedAge": a("0..1", "Range"), "criterionIds": a("0..*", "EligibilityCriterion", R),
                    "characteristics": a("0..*", "Characteristic"), "indicationIds": a("0..*", "Indication", R), **NOTES},
    "StudyDefinitionDocument": {**NLD, "language": a("1", CODE), "type": a("1", CODE), "templateName": a("1", S),
                                "versions": a("0..*", "StudyDefinitionDocumentVersion"),
                                "childIds": a("0..*", "StudyDefinitionDocument", R), **NOTES},
    "StudyDefinitionDocumentVersion": {"version": a("1", S), "status": a("1", CODE),
                                       "dateValues": a("0..*", "GovernanceDate"), "contents": a("0..*", "NarrativeContent"), **NOTES},
    "StudyDesignPopulation": {**NLD, "includesHealthySubjects": a("1", B), "plannedEnrollmentNumber": a("0..1", "Quantity|Range"),
                              "plannedCompletionNumber": a("0..1", "Quantity|Range"), "plannedSex": a("0..2", CODE),
                              "plannedAge": a("0..1", "Range"), "criterionIds": a("0..*", "EligibilityCriterion", R),
                              "cohorts": a("0..*", "StudyCohort"), **NOTES},
    "StudyElement": {**NLD, "transitionStartRule": a("0..1", "TransitionRule"), "transitionEndRule": a("0..1", "TransitionRule"),
                     "studyInterventionIds": a("0..*", "StudyIntervention", R), **NOTES},
    "StudyEpoch": {**NLD, "type": a("1", CODE), "previousId": a("0..1", "StudyEpoch", R), "nextId": a("0..1", "StudyEpoch", R), **NOTES},
    "StudyIdentifier": {"text": a("1", S), "scopeId": a("1", "Organization", R)},
    "StudyIntervention": {**NLD, "role": a("1", CODE), "type": a("1", CODE), "minimumResponseDuration": a("0..1", "Quantity"),
                          "codes": a("0..*", CODE), "administrations": a("0..*", "Administration"),
                          "productDesignation": a("0..1", CODE), "pharmacologicClass": a("0..1", CODE), **NOTES},
    "StudyRole": {**NLD, "code": a("1", CODE), "appliesToIds": a("0..*", "StudyVersion|StudyDesign", R),
                  "assignedPersons": a("0..*", "AssignedPerson"), "organizationIds": a("0..*", "Organization", R),
                  "masking": a("0..1", "Masking"), **NOTES},
    "StudyTitle": {"text": a("1", S), "type": a("1", CODE)},
    "StudyVersion": {"versionIdentifier": a("1", S), "rationale": a("1", S), "documentVersionIds": a("0..*", "StudyDefinitionDocumentVersion", R),
                     "dateValues": a("0..*", "GovernanceDate"), "amendments": a("0..*", "StudyAmendment"),
                     "businessTherapeuticAreas": a("0..*", CODE), "studyIdentifiers": a("1..*", "StudyIdentifier"),
                     "referenceIdentifiers": a("0..*", "ReferenceIdentifier"), "studyDesigns": a("0..*", "InterventionalStudyDesign|ObservationalStudyDesign"),
                     "titles": a("1..*", "StudyTitle"), "eligibilityCriterionItems": a("0..*", "EligibilityCriterionItem"),
                     "narrativeContentItems": a("0..*", "NarrativeContentItem"), "abbreviations": a("0..*", "Abbreviation"),
                     "roles": a("0..*", "StudyRole"), "organizations": a("0..*", "Organization"),
                     "studyInterventions": a("0..*", "StudyIntervention"), "administrableProducts": a("0..*", "AdministrableProduct"),
                     "medicalDevices": a("0..*", "MedicalDevice"), "productOrganizationRoles": a("0..*", "ProductOrganizationRole"),
                     "biomedicalConcepts": a("0..*", "BiomedicalConcept"), "bcCategories": a("0..*", "BiomedicalConceptCategory"),
                     "bcSurrogates": a("0..*", "BiomedicalConceptSurrogate"), "dictionaries": a("0..*", "SyntaxTemplateDictionary"),
                     "conditions": a("0..*", "Condition"), **NOTES},
    "Study": {**NLD, "versions": a("0..*", "StudyVersion"), "documentedBy": a("0..*", "StudyDefinitionDocument")},
    "Timing": {**NLD, "type": a("1", CODE), "value": a("1", S), "valueLabel": a("1", S), "relativeToFrom": a("1", CODE),
               "relativeFromScheduledInstanceId": a("1", "ScheduledActivityInstance|ScheduledDecisionInstance", R),
               "relativeToScheduledInstanceId": a("0..1", "ScheduledActivityInstance|ScheduledDecisionInstance", R),
               "windowLower": a("0..1", S), "windowUpper": a("0..1", S), "windowLabel": a("0..1", S)},
    "TransitionRule": {**NLD, "text": a("1", S)},
}

def main(src, out):
    ds = json.load(open(src, encoding="utf-8"))
    spec = {}
    for cls, body in ds.items():
        attrs = {}
        for name, v in body.get("Attributes", {}).items():
            attrs[name] = {"card": str(v.get("Cardinality", "0..1")),
                           "types": [t.get("$ref", "").replace("#/", "") for t in v.get("Type", [])],
                           "rel": "R" if v.get("Relationship Type") == "Ref" else "V"}
        spec[cls] = {"source": "CDISC DDF-RA dataStructure.yml (MIT)", "attributes": attrs}
    for cls, attrs in MANUAL.items():
        if cls in spec:
            continue
        spec[cls] = {"source": "manual (USDM v4 spec, checked vs CTX101 example)", "attributes": {**COMMON, **attrs}}
    json.dump(spec, open(out, "w", encoding="utf-8"), indent=1)
    print(f"{len(spec)} classes -> {out}")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
