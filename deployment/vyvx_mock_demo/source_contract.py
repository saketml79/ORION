"""Read-only bounded XML discovery for the explicitly authorized synthetic pilot."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = "wkf_Load_AE2E_VYVX_HIST_PREP_PULL"
MAPPINGS = {
    "m_LOAD_AE2E_VYVX_BTP_PREP", "m_Load_AE2E_VYVX_VBUS_PREP",
    "m_LOAD_AE2E_JOB_LOG_CHECK",
    "m_Check_Dependency_Status",
}
PACKAGE_SHA = "08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba"

def snapshot(element):
    return {"tag": element.tag, "attrs": dict(element.attrib),
            "children": [snapshot(child) for child in element]}

def read_contract():
    path = ROOT / "input/LumenExample.XML"
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != PACKAGE_SHA:
        raise ValueError("XML package changed; re-review required")
    selected, definitions, sessions = {}, [], []
    folder = repository = None
    # Keep each complete top-level definition until its end; clear afterwards.
    stack = []
    for event, element in ET.iterparse(path, events=("start", "end")):
        if event == "start":
            stack.append(element.tag)
            if element.tag == "REPOSITORY":
                repository = element.get("NAME")
            if element.tag == "FOLDER":
                folder = element.get("NAME")
            continue
        if len(stack) >= 2 and stack[-2] == "FOLDER":
            name = element.get("NAME", "")
            if folder == "B_AE2E":
                if element.tag == "MAPPING" and name in MAPPINGS:
                    if name in selected:
                        raise ValueError("Ambiguous mapping")
                    selected[name] = snapshot(element)
                elif element.tag == "WORKFLOW" and name == WORKFLOW:
                    selected[WORKFLOW] = snapshot(element)
                elif element.tag in {"SOURCE", "TARGET"}:
                    definitions.append({"repository": repository, "folder": folder,
                                        **snapshot(element)})
                elif element.tag == "SESSION" and ("VYVX" in name or name == "s_m_Check_Dependency_Status"):
                    sessions.append(snapshot(element))
            element.clear()
        stack.pop()
    if set(selected) != MAPPINGS | {WORKFLOW}:
        raise ValueError(f"Missing selected definitions: {(MAPPINGS | {WORKFLOW}) - set(selected)}")
    used = {child["attrs"].get("TRANSFORMATION_NAME")
            for mapping in selected.values() for child in mapping["children"]
            if child["tag"] == "INSTANCE" and child["attrs"].get("TYPE") in {"SOURCE", "TARGET"}}
    return {"package_sha256": PACKAGE_SHA, "repository": "PCREPO_PRD1_01",
            "folder": "B_AE2E", "selected": selected, "sessions": sessions,
            "definitions": [d for d in definitions if d["attrs"].get("NAME") in used]}

if __name__ == "__main__":
    import sys
    view = sys.argv[1] if len(sys.argv) > 1 else "queries"
    contract = read_contract()
    for name, obj in contract["selected"].items():
        print("\nDEFINITION", name)
        for child in obj["children"]:
            if view == "brief":
                a = child["attrs"]
                if child["tag"] == "CONNECTOR" and "PREP" in a.get("TOINSTANCE", "") and a.get("TOINSTANCE", "").endswith("INS"):
                    print(a.get("FROMINSTANCE"), a.get("FROMFIELD"), "->", a.get("TOINSTANCE"), a.get("TOFIELD"))
                if child["tag"] == "TRANSFORMATION":
                    for item in child["children"]:
                        b = item["attrs"]
                        if item["tag"] == "TRANSFORMFIELD" and b.get("PORTTYPE") in {"LOCAL VARIABLE", "OUTPUT"}:
                            print(b.get("NAME"), "=", b.get("EXPRESSION"))
                continue
            if child["tag"] == "TRANSFORMATION":
                print("TRANSFORM", child["attrs"])
                for item in child["children"]:
                    a = item["attrs"]
                    if view == "queries" and item["tag"] == "TABLEATTRIBUTE" and a.get("NAME") in {"Sql Query", "Source Filter", "Join Condition", "Pre SQL", "Post SQL", "Join Type"} and a.get("VALUE"):
                        print(a)
                    elif view == "expressions" and item["tag"] == "TRANSFORMFIELD" and a.get("EXPRESSION"):
                        print(a)
            elif view == "connectors" and child["tag"] in {"INSTANCE", "CONNECTOR", "WORKFLOWLINK", "TASK", "ATTRIBUTE"}:
                print(child["tag"], child["attrs"])
    if view != "tables":
        sys.exit(0)
    for obj in contract["definitions"]:
        print("\nTABLE", obj["attrs"])
        print([(c["attrs"].get("NAME"), c["attrs"].get("DATATYPE"),
                c["attrs"].get("PRECISION"), c["attrs"].get("SCALE"))
               for c in obj["children"] if c["tag"] in {"SOURCEFIELD", "TARGETFIELD"}])
    for obj in contract["sessions"]:
        print("\nSESSION", obj["attrs"])
        for child in obj["children"]:
            if child["tag"] in {"SESSTRANSFORMATIONINST", "SESSIONEXTENSION", "ATTRIBUTE"}:
                print(json.dumps(child))