
from typing import Any, Union
from ir.ir_schema import SessionIR


def generate_sql(session_ir: Union[dict, SessionIR], mapping_ir: Union[dict, Any], source: str, target: str) -> str:
    """
    Generate INSERT ... SELECT SQL from session and mapping IR.
    
    Handles both dict (legacy) and SessionIR/MappingIR (new) formats.
    """
    # Handle session_ir (dict or SessionIR object)
    if isinstance(session_ir, SessionIR):
        write_mode = session_ir.write_mode.value if hasattr(session_ir.write_mode, 'value') else str(session_ir.write_mode)
    else:
        write_mode = session_ir.get("write_mode", "append")
    
    # Handle mapping_ir (dict or MappingIR object)
    if hasattr(mapping_ir, 'projections'):
        # MappingIR object
        projected = [p.output_column if hasattr(p, 'output_column') else str(p) 
                    for p in (mapping_ir.projections or [])]
    else:
        # dict
        projected = [str(col) for col in (mapping_ir.get("columns") or [])]
    
    if not projected:
        projected = ["/* TODO: infer columns */"]

    select_clause = ", ".join(projected)
    insert_keyword = "INSERT OVERWRITE TABLE" if write_mode == "overwrite" else "INSERT INTO"
    return f"{insert_keyword} {target} SELECT {select_clause} FROM {source}"
