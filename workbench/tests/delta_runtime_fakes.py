"""Offline Statement API backend, including committed generation selection."""
from workbench.tests.test_delta_state import StatementClient


class RuntimeStatementClient(StatementClient):
    def download(self, path):
        from databricks.sdk.errors import NotFound
        if path not in self.objects:
            raise NotFound("absent")
        return super().download(path)

    def execute(self, **kw):
        sql = kw["statement"]
        if " UNION ALL " in sql:
            import re
            self.calls.append(kw)
            params = {p.name: p.value for p in kw["parameters"]}
            tables = re.findall(r"SELECT '([a-z_]+)' AS table_name", sql)
            return self.result([[table, str(sum(all(r[k] == v for k, v in params.items())
                                               for name, items in self.rows.items() if name.endswith('.' + table)
                                               for r in items))] for table in tables])
        if "/* hosted audit tail */" in sql:
            self.calls.append(kw)
            params = {p.name: p.value for p in kw["parameters"]}
            scope = {k: v for k, v in params.items() if not k.startswith(("path", "seq", "cursor_"))}
            baselines = {v: int(params["seq" + k[4:]]) for k, v in params.items() if k.startswith("path")}
            rows = [r for name, items in self.rows.items() if name.endswith(".audit_events")
                    for r in items if all(r[k] == v for k, v in scope.items())
                    and r["sequence"] > baselines.get(r["source_path"], 0)]
            key = lambda r: (r["source_path"], r["sequence"], r["record_id"])
            rows.sort(key=key)
            if "cursor_path" in params:
                cursor = (params["cursor_path"], int(params["cursor_sequence"]), params["cursor_id"])
                rows = [r for r in rows if key(r) > cursor]
            from workbench.delta_state import COLUMNS
            return self.result([[str(r[k]) for k in COLUMNS] for r in rows[:50]])
        if "ORDER BY sequence DESC LIMIT 2" not in sql:
            return super().execute(**kw)
        self.calls.append(kw)
        params = {p.name: p.value for p in kw["parameters"]}
        table = ".model_routes" if ".`model_routes`" in sql else ".migration_receipts"
        rows = [r for name, items in self.rows.items() if name.endswith(table)
                for r in items if all(r[k] == v for k, v in params.items())]
        rows.sort(key=lambda r: r["sequence"], reverse=True)
        from workbench.delta_state import COLUMNS
        return self.result([[str(r[k]) for k in COLUMNS] for r in rows[:2]])