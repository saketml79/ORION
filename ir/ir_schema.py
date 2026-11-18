"""
Unified Intermediate Representation (IR) schema.

Defines business-aware data structures that combine:
- Mapping semantics (transformation logic, column lineage, join grain)
- Session semantics (execution mode, commit strategy, error handling)
- Workflow semantics (dependencies, roles, orchestration intent)

All IR objects are LLM-consumable: JSON-serializable with full context.
"""
from dataclasses import dataclass, asdict, field
from typing import Any, Optional
from enum import Enum
import json


class InsertMode(Enum):
    """Execution mode for target writes."""
    APPEND = "append"
    OVERWRITE = "overwrite"
    UPDATE = "update"
    DELETE = "delete"
    UPSERT = "upsert"

    @classmethod
    def from_attributes(cls, session_attrs: dict, session_exts: list) -> "InsertMode":
        """Infer write mode from session/extension attributes."""
        all_attrs = {**session_attrs}
        for ext in session_exts:
            all_attrs.update(ext)

        attrs_str = " ".join(str(v).upper() for v in all_attrs.values())

        if "TRUNCATE" in attrs_str:
            return cls.OVERWRITE
        if "UPDATE" in attrs_str:
            return cls.UPDATE
        if "DELETE" in attrs_str:
            return cls.DELETE
        if "UPSERT" in attrs_str:
            return cls.UPSERT
        return cls.APPEND


class NodeRole(Enum):
    """Semantic role of a workflow node."""
    SESSION = "session"  # Data-load task
    GATE = "gate"  # Dependency check, condition evaluator
    CONTROL = "control"  # Orchestration and flow management
    ORCHESTRATOR = "orchestrator"  # Dummy/placeholder for control logic
    PRECONDITION = "precondition"  # Pre-flight check
    POSTCONDITION = "postcondition"  # Cleanup or validation
    METADATA = "metadata"  # Start/Success/Failure framework nodes
    UNKNOWN = "unknown"


@dataclass
class ColumnLineage:
    """Track column flow through transformations."""
    output_column: str
    input_columns: list[str] = field(default_factory=list)
    expression: Optional[str] = None
    transformation_type: Optional[str] = None  # "expression", "aggregation", "window", etc.

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class JoinSpec:
    """Details of a SQL join operation."""
    table: str
    join_type: str  # "INNER", "LEFT", "RIGHT", "FULL"
    condition: str  # SQL condition expression
    is_on_primary_key: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MappingIR:
    """Step 5: Mapping Semantic Representation.
    
    Captures data transformation logic with full semantic detail
    from sqlglot AST parsing.
    """
    name: str
    source_tables: list[str] = field(default_factory=list)
    target_table: Optional[str] = None
    
    # AST-level semantics
    joins: list[JoinSpec] = field(default_factory=list)
    where_clause: Optional[str] = None
    projections: list[ColumnLineage] = field(default_factory=list)
    aggregations: list[dict] = field(default_factory=list)  # [{"function": "sum", "expression": "..."}]
    group_by_columns: list[str] = field(default_factory=list)
    order_by_columns: list[str] = field(default_factory=list)
    
    # Transformation classification
    has_aggregation: bool = False
    has_join: bool = False
    has_filter: bool = False
    has_window_function: bool = False
    complexity_score: int = 0
    
    # SQL snippets and AST
    sql_snippets: list[dict] = field(default_factory=list)  # [{"attribute": "...", "sql": "..."}]
    metadata: dict = field(default_factory=dict)  # Original mapping attributes

    def to_dict(self) -> dict:
        result = asdict(self)
        result["joins"] = [j.to_dict() if hasattr(j, "to_dict") else j for j in self.joins]
        result["projections"] = [p.to_dict() if hasattr(p, "to_dict") else p for p in self.projections]
        return result


@dataclass
class SessionIR:
    """Step 6: Session Semantic Representation.
    
    Execution semantics for data load: write mode, commit strategy,
    error handling, linked to mapping and workflow context.
    """
    name: str
    mapping_name: str
    
    # Execution mode
    write_mode: InsertMode = InsertMode.APPEND
    commit_strategy: str = "auto"  # "auto", "manual", "distributed"
    error_handling: str = "fail"  # "fail", "continue", "notify"
    
    # Parallelization
    can_parallelize: bool = True
    parallelism_hints: dict = field(default_factory=dict)
    
    # Enrichment
    mapping_ir: Optional[MappingIR] = None
    metadata: dict = field(default_factory=dict)  # Original session attributes

    def to_dict(self) -> dict:
        result = asdict(self)
        result["write_mode"] = self.write_mode.value
        result["mapping_ir"] = self.mapping_ir.to_dict() if self.mapping_ir else None
        return result


@dataclass
class TaskNode:
    """Node in a workflow DAG."""
    name: str
    task_type: str  # "Session", "Start", "Gate", "Log", etc.
    enabled: bool = True
    role: NodeRole = NodeRole.UNKNOWN
    
    # Session details (if applicable)
    session_ir: Optional[SessionIR] = None
    
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        result = {
            "name": self.name,
            "task_type": self.task_type,
            "enabled": self.enabled,
            "role": self.role.value,
            "session_ir": self.session_ir.to_dict() if self.session_ir else None,
            "metadata": self.metadata,
        }
        return result


@dataclass
class TaskEdge:
    """Dependency edge in a workflow DAG."""
    source: str
    target: str
    condition: Optional[str] = None
    is_on_success: bool = True
    is_on_error: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WorkflowIR:
    """Step 7: Workflow Semantic Representation.
    
    Orchestration intent with normalized structure, cycle-free DAG,
    and semantic roles for all nodes.
    """
    name: str
    
    # DAG structure
    nodes: list[TaskNode] = field(default_factory=list)
    edges: list[TaskEdge] = field(default_factory=list)
    
    # Cycle information
    has_cycles: bool = False
    cycles: list[list[str]] = field(default_factory=list)
    
    # Workflow classification
    entry_nodes: list[str] = field(default_factory=list)
    terminal_nodes: list[str] = field(default_factory=list)
    data_load_sessions: list[str] = field(default_factory=list)
    
    # Normalization hints
    is_dummy_workflow: bool = False
    is_gate_workflow: bool = False
    is_pure_orchestrator: bool = False
    
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        result = {
            "name": self.name,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "has_cycles": self.has_cycles,
            "cycles": self.cycles,
            "entry_nodes": self.entry_nodes,
            "terminal_nodes": self.terminal_nodes,
            "data_load_sessions": self.data_load_sessions,
            "is_dummy_workflow": self.is_dummy_workflow,
            "is_gate_workflow": self.is_gate_workflow,
            "is_pure_orchestrator": self.is_pure_orchestrator,
            "metadata": self.metadata,
        }
        return result


@dataclass
class EstateIR:
    """Step 8: Estate-Level Representation.
    
    Cross-workflow dependencies, data product structure,
    optimization hints for master DAG construction.
    """
    workflows: list[WorkflowIR] = field(default_factory=list)
    
    # Cross-workflow relationships
    data_product_dependencies: dict = field(default_factory=dict)  # {product: [dependencies]}
    shared_mappings: dict = field(default_factory=dict)  # {mapping: [workflows_using_it]}
    
    # Optimization insights
    redundant_loads: list[str] = field(default_factory=list)
    mergeable_workflows: list[list[str]] = field(default_factory=list)
    
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        result = {
            "workflows": [w.to_dict() for w in self.workflows],
            "data_product_dependencies": self.data_product_dependencies,
            "shared_mappings": self.shared_mappings,
            "redundant_loads": self.redundant_loads,
            "mergeable_workflows": self.mergeable_workflows,
            "metadata": self.metadata,
        }
        return result


def ir_to_json_str(ir_obj) -> str:
    """Serialize IR object to JSON string for LLM prompts."""
    if hasattr(ir_obj, "to_dict"):
        return json.dumps(ir_obj.to_dict(), indent=2)
    return json.dumps(asdict(ir_obj), indent=2)
