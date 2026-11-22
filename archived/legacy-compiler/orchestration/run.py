
import json
import os
import re
import sys
from typing import Any
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Import pipeline stages
from parser.xml_scan import scan_xml
from model.registry import build_registry
from ir.mapping_ir import mapping_ir_from_snippets
from ir.session_ir import session_ir
from ir.workflow_ir import workflow_ir
from ir.ir_schema import MappingIR, SessionIR, WorkflowIR, TaskNode, TaskEdge, NodeRole
from optimizer.optimizer import optimize_workflow
from codegen.notebookgen import generate_notebook
from codegen.workflowgen import generate_workflow
from codegen.llm_codegen import generate_sql_from_ir, generate_notebook_from_ir
from visual.graphjson import workflow_graph
from visual.mermaid_renderer import (
	render_workflow_mermaid,
	render_estate_mermaid,
	render_dependency_table,
	render_mermaid_html,
)


def safe_name(name):
	allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
	return "".join(ch if ch in allowed else "_" for ch in name)


def ensure_output_dirs():
	os.makedirs("output/registry", exist_ok=True)
	os.makedirs("output/ir", exist_ok=True)
	os.makedirs("output/optimized", exist_ok=True)
	os.makedirs("output/databricks/sql", exist_ok=True)
	os.makedirs("output/databricks/sql_original", exist_ok=True)
	os.makedirs("output/databricks/notebooks", exist_ok=True)
	os.makedirs("output/databricks/workflows", exist_ok=True)
	os.makedirs("output/visual", exist_ok=True)
	os.makedirs("output/validation", exist_ok=True)


def clean_legacy_outputs():
	"""Remove hardcoded artifacts from previous runs."""
	legacy_paths = [
		"output/ir/mapping.json",
		"output/ir/session.json",
		"output/ir/workflow.json",
		"output/optimized/workflow.json",
		"output/databricks/sql/load.sql",
		"output/databricks/notebooks/load.py",
		"output/databricks/workflows/workflow.json",
		"output/databricks/workflows/graph.json",
	]
	for path in legacy_paths:
		if os.path.exists(path):
			try:
				os.remove(path)
			except:
				pass
	# Remove all YAML files
	import glob
	for yaml_file in glob.glob("output/databricks/workflows/*.yaml"):
		try:
			os.remove(yaml_file)
		except:
			pass


def write_json(path, payload):
	with open(path, "w", encoding="utf-8") as handle:
		json.dump(payload, handle, indent=2)


# YAML output removed - use JSON only for Step 8


def group_by(items, key):
	grouped = {}
	for item in items:
		grouped.setdefault(item.get(key), []).append(item)
	return grouped


def build_workflow_links(links):
	grouped = {}
	for link in links.values():
		workflow = link.get("WORKFLOWNAME")
		if not workflow:
			continue
		grouped.setdefault(workflow, []).append(link)
	return grouped


def build_step_1_bindings(scan):
	sessions = scan["execution"]["sessions"]
	workflows = scan["orchestration"]["workflows"]
	session_to_mapping = {name: attrs.get("MAPPINGNAME") for name, attrs in sessions.items()}

	workflow_bindings = {}
	for wf_name, wf_data in workflows.items():
		task_bindings = []
		for task_name, task in wf_data["taskinstances"].items():
			session_name = None
			mapping_name = None
			if task.get("TASKTYPE") == "Session":
				session_name = task.get("TASKNAME") or task_name
				mapping_name = session_to_mapping.get(session_name)
			task_bindings.append(
				{
					"task_name": task_name,
					"task_type": task.get("TASKTYPE"),
					"session_name": session_name,
					"mapping_name": mapping_name,
					"enabled": task.get("ISENABLED", "YES") == "YES",
				}
			)
		workflow_bindings[wf_name] = task_bindings

	return {
		"session_to_mapping": session_to_mapping,
		"workflow_bindings": workflow_bindings,
	}


def classify_estate_patterns(workflows):
	patterns = {
		"gate_or_control": [],
		"data_sessions": [],
		"other": [],
	}
	for wf in workflows:
		for node in wf.get("normalized_nodes", []):
			role = node.get("role")
			key = "other"
			if role in {"gate", "control", "orchestrator"}:
				key = "gate_or_control"
			elif role == "session":
				key = "data_sessions"
			patterns[key].append({"workflow": wf["name"], "task": node["name"], "role": role})
	return patterns


def build_cross_workflow_dependencies(workflows: list[dict[str, Any]]) -> dict[str, set[str]]:
	"""Map each workflow to the workflows it explicitly calls via command tasks."""
	workflow_names = [wf.get("name") for wf in workflows if wf.get("name")]
	workflow_name_map = {name.lower(): name for name in workflow_names}
	pattern = re.compile(r"(wkf_[A-Za-z0-9_]+)", re.IGNORECASE)
	depends_on: dict[str, set[str]] = {name: set() for name in workflow_names}

	for wf in workflows:
		wf_name = wf.get("name")
		if not wf_name:
			continue

		for node in wf.get("nodes", []):
			if (node.get("task_type") or "").lower() != "command":
				continue

			candidate_texts = []
			node_name = node.get("name")
			if isinstance(node_name, str):
				candidate_texts.append(node_name)

			attrs = node.get("attrs") or {}
			if isinstance(attrs, dict):
				for value in attrs.values():
					if isinstance(value, str):
						candidate_texts.append(value)

			for text in candidate_texts:
				for match in pattern.finditer(text):
					called_raw = match.group(1)
					called_name = workflow_name_map.get(called_raw.lower())
					if called_name and called_name != wf_name:
						depends_on[wf_name].add(called_name)

	return depends_on


def generate_master_orchestrator(workflow_jobs: list[dict[str, Any]], depends_on_workflows: dict[str, set[str]]) -> dict[str, Any]:
	"""Generate a Databricks run_job_task-style master orchestration JSON."""
	job_names = {job.get("name") for job in workflow_jobs if job.get("name")}
	workflow_to_task_key = {
		name: f"run__{safe_name(name)}"
		for name in sorted(job_names)
	}

	tasks = []
	for workflow_name in sorted(job_names):
		parents = sorted(dep for dep in depends_on_workflows.get(workflow_name, set()) if dep in job_names)
		task = {
			"task_key": workflow_to_task_key[workflow_name],
			"description": f"Run child workflow job: {workflow_name}",
			"run_job_task": {
				# Placeholder to be replaced with concrete Databricks Job ID during deployment.
				"job_id": f"${{jobs.{workflow_name}}}",
			},
			"depends_on": [{"task_key": workflow_to_task_key[parent]} for parent in parents],
		}
		tasks.append(task)

	dependency_edges = []
	for child, parents in sorted(depends_on_workflows.items()):
		for parent in sorted(parents):
			if child in job_names and parent in job_names:
				dependency_edges.append({"workflow": child, "depends_on": parent})

	return {
		"name": "master_orchestrator",
		"description": "Cross-workflow parent-before-child orchestration generated from cmd_Call_wkf dependencies.",
		"tasks": tasks,
		"metadata": {
			"job_id_placeholders": "Replace ${jobs.<workflow_name>} with actual Databricks child job IDs.",
			"workflow_count": len(job_names),
			"dependency_edge_count": len(dependency_edges),
			"dependencies": dependency_edges,
		},
	}



# ============================================================================
# MAIN PIPELINE - Stages 0-8
# ============================================================================

if __name__ == "__main__":
	if len(sys.argv) < 2:
		print("Usage: python run.py <path_to_informatica_xml>")
		sys.exit(1)

	xml_path = sys.argv[1]
	
	print(f"Starting Informatica to Databricks migration pipeline...")
	print(f"Input: {xml_path}")
	
	# Setup
	ensure_output_dirs()
	clean_legacy_outputs()

	# STAGE 0: Full structural scan
	print("\n[STAGE 0] Scanning Informatica XML...")
	scan = scan_xml(xml_path)
	write_json("output/registry/step0_scan_registry.json", scan)
	
	registry = build_registry(scan)
	write_json("output/registry/registry.json", registry)
	print(f"  Inventory: {len(scan['definitions']['mappings'])} mappings, "
		  f"{len(scan['execution']['sessions'])} sessions, "
		  f"{len(scan['orchestration']['workflows'])} workflows")

	# STAGE 1: Binding and relationship resolution
	print("\n[STAGE 1] Resolving relationships...")
	bindings = build_step_1_bindings(scan)
	write_json("output/ir/step1_bindings.json", bindings)
	print(f"  Bound {len(bindings['workflow_bindings'])} workflows to sessions and mappings")

	# STAGE 2-4: Workflow DAG construction, normalization
	print("\n[STAGES 2-4] Processing workflow DAGs...")
	workflow_links_by_name = build_workflow_links(scan["orchestration"]["workflowlinks"])
	workflow_irs = []
	optimized_workflows = []

	for workflow_name, workflow_data in scan["orchestration"]["workflows"].items():
		wir = workflow_ir(
			workflow_name,
			workflow_data["taskinstances"],
			workflow_links_by_name.get(workflow_name, workflow_data.get("workflowlinks", {})),
		)
		optimized = optimize_workflow(wir)
		workflow_irs.append(wir)
		optimized_workflows.append(optimized)

		wf_key = safe_name(workflow_name)
		write_json(f"output/optimized/workflow_{wf_key}.json", optimized)

	write_json("output/ir/workflows.json", workflow_irs)
	write_json("output/optimized/workflows.json", optimized_workflows)
	estate_patterns = classify_estate_patterns(optimized_workflows)
	write_json("output/optimized/estate_patterns.json", estate_patterns)
	print(f"  Processed {len(optimized_workflows)} workflows")

	# STAGE 5: Mapping semantic extraction (sqlglot AST → IR)
	print("\n[STAGE 5] Extracting mapping semantics from sqlglot AST...")
	sql_by_mapping = group_by(scan["sql_snippets"], "mapping")
	mapping_irs_dict = {}
	for mapping_name, mapping_attrs in scan["definitions"]["mappings"].items():
		mir = mapping_ir_from_snippets(mapping_name, mapping_attrs, sql_by_mapping.get(mapping_name, []))
		mapping_irs_dict[mapping_name] = mir

	# Serialize mapping IRs
	mapping_irs_serialized = [mir.to_dict() for mir in mapping_irs_dict.values()]
	write_json("output/ir/mappings_ir.json", mapping_irs_serialized)
	print(f"  Generated MappingIR for {len(mapping_irs_dict)} mappings")

	# STAGE 6: Session semantic enrichment
	print("\n[STAGE 6] Enriching sessions with execution semantics...")
	session_extensions = list(scan["execution"]["sessionextensions"].values())
	ext_by_session = group_by(session_extensions, "session")
	session_irs_dict = {}
	for session_name, session_attrs in scan["execution"]["sessions"].items():
		mapping_name = session_attrs.get("MAPPINGNAME")
		extensions = [x.get("attrs", x) for x in ext_by_session.get(session_name, [])]
		mapping_ir_obj = mapping_irs_dict.get(mapping_name)
		sir = session_ir(session_name, mapping_name, session_attrs, extensions, mapping_ir_obj)
		session_irs_dict[session_name] = sir

	# Serialize session IRs
	session_irs_serialized = [sir.to_dict() for sir in session_irs_dict.values()]
	write_json("output/ir/sessions_ir.json", session_irs_serialized)
	print(f"  Enriched {len(session_irs_dict)} sessions with execution context")

	# STAGE 7: Estate-level DAG and optimization summary
	print("\n[STAGE 7] Building estate-level dependency DAG...")
	print(f"  Estate patterns classified: {len(estate_patterns['data_sessions'])} data sessions, "
		  f"{len(estate_patterns['gate_or_control'])} control tasks")

	# Visual artifacts
	print("\n[VISUAL] Generating dependency diagrams...")
	for optimized in optimized_workflows:
		wf_key = safe_name(optimized.get("name", ""))
		mermaid_diagram = render_workflow_mermaid(optimized.get("name", ""), optimized)
		with open(f"output/visual/workflow_{wf_key}.md", "w", encoding="utf-8") as f:
			f.write(mermaid_diagram)
		workflow_html = render_mermaid_html(f"Workflow DAG - {optimized.get('name', '')}", mermaid_diagram)
		with open(f"output/visual/workflow_{wf_key}.html", "w", encoding="utf-8") as f:
			f.write(workflow_html)

	# Estate-level diagram
	estate_diagram = render_estate_mermaid(optimized_workflows)
	with open("output/visual/estate_dependencies.md", "w", encoding="utf-8") as f:
		f.write(estate_diagram)
	estate_html = render_mermaid_html("Estate Dependency DAG", estate_diagram)
	with open("output/visual/estate_dependencies.html", "w", encoding="utf-8") as f:
		f.write(estate_html)

	# Dependency table
	dep_table = render_dependency_table(optimized_workflows)
	with open("output/visual/workflow_summary.md", "w", encoding="utf-8") as f:
		f.write(dep_table)

	print(f"  Generated visual artifacts in output/visual/")

	# STAGE 8: Artifact generation from IR (with LLM fallback)
	print("\n[STAGE 8] Generating executable code from IR...")
	depends_on_workflows = build_cross_workflow_dependencies(optimized_workflows)
	all_workflow_jobs = []
	all_graphs = []

	for optimized in optimized_workflows:
		workflow_name = optimized["name"]
		wf_key = safe_name(workflow_name)
		notebook_tasks = []
		session_task_key_by_name = {}

		for node in optimized.get("normalized_nodes", []):
			if node.get("task_type") != "Session":
				continue
			
			session_name = node["name"]
			session_obj = session_irs_dict.get(session_name)
			if not session_obj:
				continue

			mapping_name = session_obj.mapping_name
			mapping_ir_obj = mapping_irs_dict.get(mapping_name)
			if not mapping_ir_obj:
				continue

			task_key = f"{wf_key}__{safe_name(session_name)}"
			source_schema = "raw"
			target_schema = "cur"

			# [CHANGE] Use LLM-aware code generation (or fallback with full SQL semantics)
			sql = generate_sql_from_ir(mapping_ir_obj, session_obj, source_schema, target_schema)

			# [CHANGE] Save original Informatica SQL (if available)
			original_sql = ""
			if mapping_ir_obj.sql_snippets:
				best_sql = ""
				best_score = -1
				for snippet in mapping_ir_obj.sql_snippets:
					sql_text = (snippet.get("sql") or "").strip()
					if not sql_text:
						continue
					sql_lower = sql_text.lower()
					score = len(sql_text)
					if " join " in sql_lower:
						score += 500
					if " where " in sql_lower:
						score += 200
					if score > best_score:
						best_score = score
						best_sql = sql_text
				original_sql = best_sql
				with open(f"output/databricks/sql_original/{task_key}_INFORMATICA.sql", "w", encoding="utf-8") as f:
					f.write(f"-- ORIGINAL SQL FROM INFORMATICA MAPPING: {mapping_name}\n")
					f.write(f"-- SESSION: {session_name}\n")
					f.write(f"-- PRESERVED FOR REFERENCE AND AUDIT\n\n")
					f.write(original_sql)

			# [CHANGE] Save generated SQL with metadata
			with open(f"output/databricks/sql/{task_key}.sql", "w", encoding="utf-8") as f:
				f.write(f"-- GENERATED DATABRICKS SQL\n")
				f.write(f"-- MAPPING: {mapping_name}\n")
				f.write(f"-- SESSION: {session_name}\n")
				f.write(f"-- GENERATED BY: llm_codegen/fallback\n")
				f.write(f"-- SOURCE SCHEMA: {source_schema}\n")
				f.write(f"-- TARGET SCHEMA: {target_schema}\n")
				if original_sql:
					f.write(f"-- ORIGINAL SQL SAVED TO: {task_key}_INFORMATICA.sql\n")
				f.write(f"\n{sql}")

			# [CHANGE] Create validation JSON for SQL comparison
			validation_record = {
				"task_key": task_key,
				"mapping_name": mapping_name,
				"session_name": session_name,
				"has_original_sql": bool(original_sql),
				"original_sql_length": len(original_sql) if original_sql else 0,
				"generated_sql_length": len(sql),
				"sql_semantics": {
					"source_tables": mapping_ir_obj.source_tables or [],
					"has_joins": bool(mapping_ir_obj.joins),
					"join_count": len(mapping_ir_obj.joins) if mapping_ir_obj.joins else 0,
					"has_where_clause": mapping_ir_obj.where_clause is not None,
					"has_aggregations": bool(mapping_ir_obj.aggregations),
					"has_group_by": bool(mapping_ir_obj.group_by_columns),
				}
			}
			with open(f"output/validation/{task_key}_SQL_VALIDATION.json", "w", encoding="utf-8") as f:
				json.dump(validation_record, f, indent=2)

			# Generate notebook
			columns = [p.output_column for p in mapping_ir_obj.projections] if mapping_ir_obj.projections else []
			notebook_code = generate_notebook(columns, sql)

			with open(f"output/databricks/notebooks/{task_key}.py", "w", encoding="utf-8") as f:
				f.write(notebook_code)

			session_task_key_by_name[session_name] = task_key
			# Edges may reference the Informatica instance name (attrs.NAME) rather
			# than the TASKNAME (node["name"]), so register both to avoid mismatches.
			instance_name = node.get("attrs", {}).get("NAME", "")
			if instance_name and instance_name != session_name:
				session_task_key_by_name[instance_name] = task_key
			notebook_tasks.append({
				"task_key": task_key,
				"notebook_path": f"/pipelines/{task_key}",
				"depends_on": [],
			})

		# Enrich depends_on from DAG edges
		notebook_task_index = {task["task_key"]: task for task in notebook_tasks}
		for edge in optimized.get("edges", []):
			source_key = session_task_key_by_name.get(edge["source"])
			target_key = session_task_key_by_name.get(edge["target"])
			if source_key and target_key and target_key in notebook_task_index:
				if source_key not in notebook_task_index[target_key]["depends_on"]:
					notebook_task_index[target_key]["depends_on"].append(source_key)

		# Annotate parallel tasks: tasks with no predecessors that start simultaneously
		root_tasks = [t for t in notebook_tasks if not t["depends_on"]]
		if len(root_tasks) > 1:
			for t in root_tasks:
				t["parallel"] = True

		# Build execution summary for the workflow
		sequential_tasks = [t for t in notebook_tasks if t["depends_on"]]
		has_decision = any(n.get("task_type") == "Decision" for n in optimized.get("nodes", []))
		if len(root_tasks) == len(notebook_tasks):
			pattern = "fully_parallel"
		elif len(root_tasks) > 1:
			pattern = "mixed"
		elif len(notebook_tasks) == 1:
			pattern = "single_task"
		else:
			pattern = "sequential"

		# Generate workflow JSON
		wf = generate_workflow(workflow_name, notebook_tasks)
		wf["depends_on_workflows"] = sorted(depends_on_workflows.get(workflow_name, set()))
		wf["execution_summary"] = {
			"pattern": pattern,
			"total_tasks": len(notebook_tasks),
			"parallel_start_tasks": len(root_tasks) if len(root_tasks) > 1 else 0,
			"sequential_tasks": len(sequential_tasks),
			"has_conditional_branching": has_decision,
		}
		wg = workflow_graph(optimized)
		all_workflow_jobs.append(wf)
		all_graphs.append({"workflow": workflow_name, "graph": wg})

		write_json(f"output/databricks/workflows/workflow_{wf_key}.json", wf)
		write_json(f"output/databricks/workflows/graph_{wf_key}.json", wg)

	write_json("output/databricks/workflows/workflows.json", all_workflow_jobs)
	write_json("output/databricks/workflows/graphs.json", all_graphs)

	master_orchestrator = generate_master_orchestrator(all_workflow_jobs, depends_on_workflows)
	write_json("output/databricks/workflows/master_orchestrator.json", master_orchestrator)

	print(f"  Generated {len(all_workflow_jobs)} workflow artifacts")
	print(f"  Generated {sum(len(w.get('tasks', [])) for w in all_workflow_jobs)} SQL files")
	print(f"  Generated {sum(len(w.get('tasks', [])) for w in all_workflow_jobs)} notebook files")
	print("  Generated master orchestrator: output/databricks/workflows/master_orchestrator.json")

	print("\n" + "="*70)
	print("[OK] Pipeline completed successfully!")
	print("="*70)
	print("\nOutput artifacts:")
	print("  Registry: output/registry/")
	print("  IR (Mapping/Session/Workflow): output/ir/")
	print("  Optimized DAGs: output/optimized/")
	print("  Executable Code: output/databricks/")
	print("  Visual Diagrams: output/visual/")
	print("\nNext steps:")
	print("  1. Review output/visual/workflow_*.md for dependency diagrams")
	print("  2. Inspect output/databricks/sql/*.sql for generated transformations")
	print("  3. Review output/databricks/workflows/*.json for orchestration")
	print("  4. Deploy to Databricks using workflows API")
	if not os.environ.get("OPENAI_API_KEY"):
		print("\n[WARNING] OpenAI API key not set (OPENAI_API_KEY env var)")
		print("   Using deterministic code generation fallback")
		print("   For LLM-optimized code, set: export OPENAI_API_KEY=sk-...")
