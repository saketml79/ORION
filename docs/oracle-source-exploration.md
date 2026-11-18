# Oracle source exploration

In Agent activity, **Explore Oracle sources** is next to **Inspect source targets**. The existing target button continues to inspect exported output targets. Select a workflow, then explicitly load catalog, schema, table and column metadata for an existing Unity Catalog foreign catalog.

The explorer uses the selected local workspace identity or the hosted caller's Databricks on-behalf-of identity. It makes bounded, permission-filtered metadata requests. Results can be incomplete, and an unknown or inaccessible catalog is not proof that a source is absent. A foreign catalog is not automatically an Oracle connection. An Oracle-format table is a metadata observation, not proof of connectivity, source identity or parity. No rows, credentials, connection options or tokens are returned.

## Prerequisites and boundaries

An administrator must separately configure an Oracle federation connection and foreign catalog, including approved Oracle credentials, catalog permissions and network access from the intended Databricks compute. The app does not create these resources or configure networking. An existing foreign catalog alone does not establish that the database is reachable or that the user can read rows.

This feature **does not save or approve a source binding**, query Oracle rows, generate an executable Oracle ingestion workflow, or relax the original-source compiler gate. The original source scenario remains advisory where the reviewed compiler does not support it. Additional design and verification are required before the app can claim live Oracle connectivity or generate a validated original-source execution path.

See [Databricks Oracle federation documentation](https://learn.microsoft.com/en-us/azure/databricks/query-federation/oracle) for connection requirements and [serverless networking guidance](https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/) for network prerequisites.