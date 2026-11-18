"""The Logs page renders independent live Databricks diagnostics quickly."""
from pathlib import Path
import re
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


STATIC = Path(__file__).resolve().parents[1] / "workbench" / "static"


def test_logs_page_shows_safe_databricks_error_and_stack_lines():
    diagnostic = {
        "id": "diagnostic-1", "timestamp": "2026-10-30T23:48:40+00:00",
        "level": "ERROR", "event": "hosted_request_failure", "method": "GET",
        "path": "/api/projects", "status_code": 503, "error_type": "DeltaStatementError",
        "error": {"error_code": "DELTA_CONCURRENT_WRITE", "sql_state": "40001",
                  "statement_id": "stmt-safe-123", "provider_message": "Concurrent Delta write"},
        "frames": [{"file": "hosted_registry.py", "function": "_audit", "line": 193}],
        "storage": "current_app_instance_memory_only",
    }
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        context = browser.new_context(offline=True, service_workers="block")
        page = context.new_page()
        errors, unexpected = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        markup = (STATIC / "index.html").read_text(encoding="utf-8")
        markup = re.sub(r'<script\b[^>]*src="([^"]+)"[^>]*>\s*</script>',
            lambda match: match[0] if match[1] == "/static/app.js" else "", markup)
        markup = re.sub(r'<link\b[^>]*>|<img\b[^>]*>', "", markup)
        markup = markup.replace("</head>", '<link rel="stylesheet" href="/static/app.css"></head>')

        def route_request(route):
            path = urlsplit(route.request.url).path
            if path == "/": route.fulfill(content_type="text/html", body=markup)
            elif path in {"/static/app.js", "/static/app.css"}: route.fulfill(path=str(STATIC / path.rsplit("/", 1)[-1]))
            elif path == "/api/health": route.fulfill(json={"status": "ok", "authentication": "databricks_obo", "local_only": False})
            elif path == "/api/hosted/logs": route.fulfill(json={"items": [diagnostic], "storage": "bounded per-user in-memory"})
            elif path == "/api/logs": route.fulfill(json={"items": [], "total": 0, "chain_valid": True, "storage": "committed audit", "limit": 50})
            elif path == "/api/input-packages": route.fulfill(json={"items": []})
            elif path == "/api/projects": route.fulfill(json={"items": []})
            else:
                unexpected.append(path); route.abort()

        context.route("**/*", route_request)
        try:
            page.goto("https://orion-diagnostics.invalid/#logs", wait_until="domcontentloaded")
            expect(page.locator("#hosted-diagnostics-summary")).to_contain_text("diagnostic events")
            panel = page.locator("#hosted-diagnostics-list")
            expect(panel).to_contain_text("GET /api/projects · HTTP 503")
            expect(panel).to_contain_text("DELTA_CONCURRENT_WRITE")
            expect(panel).to_contain_text("SQL state: 40001")
            expect(panel).to_contain_text("Statement ID: stmt-safe-123")
            expect(panel).to_contain_text("hosted_registry.py · _audit · 193")
            expect(panel).to_contain_text("Concurrent Delta write")
            assert not unexpected, unexpected
            assert not errors, errors
        finally:
            context.close(); browser.close()
