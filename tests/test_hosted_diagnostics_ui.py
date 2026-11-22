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
    service_event = {
        "sequence": 7, "event": "agent_run_completed", "timestamp": "2026-10-30T23:49:00+00:00",
        "level": "info", "message": "Agent run completed", "role": "architecture",
        "run_id": "a" * 32, "package_hash": "b" * 64, "correlation_id": "c" * 32,
        "input_tokens": 12, "output_tokens": 34, "total_tokens": 46, "usage_status": "reported",
        "previous_hash": "d" * 64, "event_hash": "e" * 64,
    }
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        context = browser.new_context(offline=True, service_workers="block")
        page = context.new_page()
        page.add_init_script("""(() => {
          window.copiedLogs = [];
          Object.defineProperty(navigator, 'clipboard', {configurable: true,
            value: {writeText: async value => window.copiedLogs.push(value)}});
        })()""")
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
            elif path == "/api/logs": route.fulfill(json={"items": [service_event], "total": 1, "chain_valid": True, "storage": "committed audit", "limit": 50})
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
            expect(panel.locator('.log-copy-button')).to_have_count(1)
            assert not unexpected, unexpected
            service = page.locator('#logs-list')
            expect(service.locator('.log-copy-button')).to_have_count(1)
            service.get_by_role('button', name='Copy complete safe log entry').click()
            expect(service.get_by_role('button', name='Log entry copied')).to_be_visible()
            page.locator('#hosted-diagnostics-list').get_by_role('button', name='Copy complete safe log entry').click()
            expect(page.locator('#hosted-diagnostics-list').get_by_role('button', name='Log entry copied')).to_be_visible()
            copied = page.evaluate('window.copiedLogs')
            assert len(copied) == 2
            assert 'agent_run_completed' in copied[0] and 'input_tokens' in copied[0]
            assert 'DELTA_CONCURRENT_WRITE' in copied[1] and 'hosted_registry.py' in copied[1]
            assert not errors, errors
        finally:
            context.close(); browser.close()
