
from ir.ir_schema import SessionIR, InsertMode, MappingIR
from typing import Optional


def _infer_write_mode(session_attrs: dict, extension_attrs: list) -> InsertMode:
    """Infer write mode from session and extension attributes."""
    combined = " ".join(
        str(v).lower()
        for v in list(session_attrs.values()) + [value for attrs in extension_attrs for value in attrs.values()]
    )
    if "truncate" in combined or "overwrite" in combined:
        return InsertMode.OVERWRITE
    if "update" in combined:
        return InsertMode.UPDATE
    if "delete" in combined:
        return InsertMode.DELETE
    if "upsert" in combined:
        return InsertMode.UPSERT
    return InsertMode.APPEND


def session_ir(
    name: str, 
    mapping_name: str, 
    session_attrs: dict, 
    session_extensions: list,
    mapping_ir_obj: Optional[MappingIR] = None,
) -> SessionIR:
    """
    Build SessionIR from session metadata and optional mapping IR.
    
    Returns a semantic representation of session execution intent.
    """
    write_mode = _infer_write_mode(session_attrs, session_extensions)
    
    sir = SessionIR(
        name=name,
        mapping_name=mapping_name,
        write_mode=write_mode,
        commit_strategy="auto",
        error_handling="fail",
        can_parallelize=True,
        mapping_ir=mapping_ir_obj,
        metadata={
            "session_attrs": session_attrs,
            "session_extensions": session_extensions,
        }
    )
    
    return sir


# Legacy wrapper for backward compatibility
def session_ir_legacy(name, mapping_name, session_attrs, session_extensions):
    """Legacy wrapper. Use session_ir() for new code."""
    sir = session_ir(name, mapping_name, session_attrs, session_extensions)
    
    return {
        "type": "session",
        "name": name,
        "mapping": mapping_name,
        "attrs": session_attrs,
        "session_extensions": session_extensions,
        "write_mode": sir.write_mode.value,
        "ir": sir,
    }
