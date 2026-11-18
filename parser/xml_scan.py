
import xml.etree.ElementTree as ET


def _sql_attr_name(attr_name):
    lowered = (attr_name or "").strip().lower()
    return lowered in {"sql query", "lookup sql override", "source filter", "pre sql", "post sql"}


def scan_xml(path):
    registry = {
        "definitions": {
            "sources": {},
            "targets": {},
            "transformations": {},
            "transform_fields": {},
            "mappings": {},
            "connectors": {},
            "mapping_instances": {},
        },
        "execution": {
            "sessions": {},
            "sesstransformationinst": {},
            "sessionextensions": {},
        },
        "orchestration": {
            "workflows": {},
            "taskinstances": {},
            "workflowlinks": {},
            "schedules": {},
        },
        "sql_snippets": [],
        "stats": {
            "counts": {},
        },
    }

    tag_counts = {}
    current = {
        "mapping": None,
        "workflow": None,
        "session": None,
        "transformation": None,
    }

    context = ET.iterparse(path, events=("start", "end"))
    for ev, el in context:
        tag = el.tag

        if ev == "start":
            if tag == "MAPPING":
                current["mapping"] = el.attrib.get("NAME")
                if current["mapping"]:
                    registry["definitions"]["mappings"][current["mapping"]] = dict(el.attrib)

            elif tag == "WORKFLOW":
                current["workflow"] = el.attrib.get("NAME")
                if current["workflow"]:
                    registry["orchestration"]["workflows"][current["workflow"]] = {
                        "attrs": dict(el.attrib),
                        "taskinstances": {},
                        "workflowlinks": [],
                    }

            elif tag == "SESSION":
                current["session"] = el.attrib.get("NAME")
                if current["session"]:
                    registry["execution"]["sessions"][current["session"]] = dict(el.attrib)

            elif tag == "TRANSFORMATION":
                current["transformation"] = el.attrib.get("NAME")
                tr_key = f"{current['mapping'] or '__global__'}::{current['transformation'] or '__unknown__'}"
                registry["definitions"]["transformations"][tr_key] = {
                    "mapping": current["mapping"],
                    "attrs": dict(el.attrib),
                }

            elif tag == "SOURCE":
                key = f"{current['mapping'] or '__global__'}::{el.attrib.get('NAME', '__unknown__')}"
                registry["definitions"]["sources"][key] = {
                    "mapping": current["mapping"],
                    "attrs": dict(el.attrib),
                }

            elif tag == "TARGET":
                key = f"{current['mapping'] or '__global__'}::{el.attrib.get('NAME', '__unknown__')}"
                registry["definitions"]["targets"][key] = {
                    "mapping": current["mapping"],
                    "attrs": dict(el.attrib),
                }

            elif tag == "TASKINSTANCE" and current["workflow"]:
                task_name = el.attrib.get("TASKNAME") or el.attrib.get("NAME")
                if task_name:
                    registry["orchestration"]["workflows"][current["workflow"]]["taskinstances"][task_name] = dict(el.attrib)
                    task_key = f"{current['workflow']}::{task_name}"
                    registry["orchestration"]["taskinstances"][task_key] = dict(el.attrib)

            elif tag == "WORKFLOWLINK" and current["workflow"]:
                registry["orchestration"]["workflows"][current["workflow"]]["workflowlinks"].append(dict(el.attrib))
                link_key = f"{current['workflow']}::{len(registry['orchestration']['workflows'][current['workflow']]['workflowlinks']) - 1}"
                registry["orchestration"]["workflowlinks"][link_key] = dict(el.attrib)

            elif tag in {"SCHEDULER", "SCHEDULEINFO"} and current["workflow"]:
                schedule_key = f"{current['workflow']}::{tag}::{len(registry['orchestration']['schedules'])}"
                registry["orchestration"]["schedules"][schedule_key] = dict(el.attrib)

        if ev == "end":
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

            if tag == "CONNECTOR":
                key = f"{current['mapping'] or '__global__'}::{len(registry['definitions']['connectors'])}"
                registry["definitions"]["connectors"][key] = {
                    "mapping": current["mapping"],
                    "attrs": dict(el.attrib),
                }

            elif tag == "INSTANCE":
                key = f"{current['mapping'] or '__global__'}::{el.attrib.get('NAME', '__unknown__')}::{len(registry['definitions']['mapping_instances'])}"
                registry["definitions"]["mapping_instances"][key] = {
                    "mapping": current["mapping"],
                    "attrs": dict(el.attrib),
                }

            elif tag == "TRANSFORMFIELD":
                tf_key = f"{current['mapping'] or '__global__'}::{current['transformation'] or '__unknown__'}::{el.attrib.get('NAME', '__unknown__')}::{len(registry['definitions']['transform_fields'])}"
                registry["definitions"]["transform_fields"][tf_key] = {
                    "mapping": current["mapping"],
                    "transformation": current["transformation"],
                    "attrs": dict(el.attrib),
                }

            elif tag == "SESSTRANSFORMATIONINST":
                key = f"{current['session'] or '__global__'}::{el.attrib.get('SINSTANCENAME', el.attrib.get('TRANSFORMATIONNAME', '__unknown__'))}::{len(registry['execution']['sesstransformationinst'])}"
                registry["execution"]["sesstransformationinst"][key] = {
                    "session": current["session"],
                    "attrs": dict(el.attrib),
                }

            elif tag == "SESSIONEXTENSION":
                key = f"{current['session'] or '__global__'}::{el.attrib.get('SINSTANCENAME', '__unknown__')}::{len(registry['execution']['sessionextensions'])}"
                registry["execution"]["sessionextensions"][key] = {
                    "session": current["session"],
                    "attrs": dict(el.attrib),
                }

            elif tag == "TABLEATTRIBUTE":
                attr_name = el.attrib.get("NAME")
                if _sql_attr_name(attr_name):
                    registry["sql_snippets"].append(
                        {
                            "mapping": current["mapping"],
                            "session": current["session"],
                            "workflow": current["workflow"],
                            "transformation": current["transformation"],
                            "attribute": attr_name,
                            "sql": el.attrib.get("VALUE", ""),
                        }
                    )

            elif tag == "MAPPING":
                current["mapping"] = None
            elif tag == "WORKFLOW":
                current["workflow"] = None
            elif tag == "SESSION":
                current["session"] = None
            elif tag == "TRANSFORMATION":
                current["transformation"] = None

            el.clear()

    registry["stats"]["counts"] = tag_counts
    return registry
