"""OBO Volume metadata via supported Apps SQL scope, no alternate identity."""
from dataclasses import replace
from types import SimpleNamespace

from databricks.sdk.errors import NotFound
from workbench.delta_state import DeltaStateStore, _identifier


class SqlVolumes:
    def __init__(self, identity, warehouse):
        self.store = DeltaStateStore(identity.client, warehouse, "unused", "unused", identity.user_id)

    def read(self, full_name):
        parts = full_name.split(".")
        if len(parts) != 3:
            raise ValueError("A catalog.schema.volume name is required")
        present = self.store._execute("SHOW VOLUMES IN " + _identifier(parts[0]) + "." + _identifier(parts[1]))
        if not any(row[1] == parts[2] for row in present):
            raise NotFound("Volume not found in authorized namespace")
        try:
            rows = self.store._execute("DESCRIBE VOLUME " + ".".join(_identifier(v) for v in parts))
        except RuntimeError:
            # Do not translate permission or SQL failures to missing resources.
            raise
        if not rows:
            raise NotFound("Volume not found")
        row = rows[0]
        return SimpleNamespace(name=row[0], catalog_name=row[1], schema_name=row[2], owner=row[3],
                               storage_location=row[4], volume_type=row[5], full_name=full_name)

    def create(self, catalog_name, schema_name, name, volume_type, **kwargs):
        if getattr(volume_type, "value", volume_type) != "MANAGED":
            raise ValueError("Only managed user Volumes can be provisioned")
        full_name = ".".join(_identifier(v) for v in (catalog_name, schema_name, name))
        self.store._execute("CREATE VOLUME IF NOT EXISTS " + full_name)
        return self.read(".".join((catalog_name, schema_name, name)))

    def list(self, catalog_name, schema_name, max_results=200, **kwargs):
        rows = self.store._execute("SHOW VOLUMES IN " + _identifier(catalog_name) + "." + _identifier(schema_name))
        return iter(SimpleNamespace(name=row[1]) for row in rows[:max_results])


class UserClient:
    def __init__(self, identity, warehouse):
        self._client = identity.client
        self.volumes = SqlVolumes(identity, warehouse)

    def __getattr__(self, name):
        return getattr(self._client, name)


def with_sql_volumes(identity, warehouse):
    return replace(identity, client=UserClient(identity, warehouse))