"""Pure canonical workflow compilation and portable download presentation."""
import io
import re
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from fastapi import HTTPException
from fastapi.responses import Response


def compile_response(index, body):
    from workbench.workflow_artifacts import compile_workflow_artifacts
    if body.consent is not True:
        raise HTTPException(403, "Explicit workflow artifact generation consent:true is required")
    if index.nodes.get(body.workflow_id, {}).get("kind") != "workflow":
        raise HTTPException(404, "Unknown canonical workflow ID")
    try:
        result = compile_workflow_artifacts(index.model, body.workflow_id, scenario=body.scenario)
    except ValueError as error:
        diagnostic = ("saved_analysis_incompatible" if str(error) == "Unsupported or changed scoped semantics; review required"
                      else "unsupported_workflow" if str(error).startswith("Unsupported workflow;")
                      else "compiler_validation_failed")
        raise HTTPException(422, str(error), headers={"X-Workbench-Diagnostic": diagnostic}) from None
    return {**result, "workflow_id": body.workflow_id, "scenario": body.scenario,
            "package_hash": index.model["package"]["sha256"],
            "deployment_allowed": False, "execution_allowed": False, "approved": False,
            "notice": "Deterministic in-memory boilerplate only. Runtime bindings and independent review required. No model invocation, execution, deployment or approval performed."}


def archive_response(result, workflow_id):
    artifacts = result["artifacts"]
    # Trusted compiler text only. Reject ambiguous POSIX/Windows relative paths.
    for path, text in artifacts.items():
        if (not isinstance(path, str) or not path
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or not re.fullmatch(r"[A-Za-z0-9_./-]+", path)
                or not isinstance(text, str)):
            raise HTTPException(422, "Invalid compiler artifact relative path or text")
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(artifacts):
            entry = ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = ZIP_DEFLATED
            archive.writestr(entry, artifacts[path].encode("utf-8"), compresslevel=9)
    member_names = {
        "00ebb24274f4376443ff0c7b721fda8ade928a58bca2d118d42fe5f741522f38": "wil_net_dis",
        "3507aed6f85c7c2dbc5dca041f6662f506e8ef0a623fa9642febf55c0ad3b203": "wil_net_ins",
        "87856a03806ee5c53746761a542b409f588d1768223106ca32486daf1bfa6fa8": "wil_cus_inst",
        "4d30896b15af879a2011782a66e1446f8de8fbd4bc1c681d14c15c5eb58d10a0": "wil_cus_dis",
    }
    filename = f"{member_names.get(workflow_id, 'workflow_artifacts')}_bronze.zip"
    return Response(buffer.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
    })