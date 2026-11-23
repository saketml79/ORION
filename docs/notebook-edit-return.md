# App-managed Databricks notebook edit return

## Scope

This feature edits a saved task notebook, not the agent mesh or model routing.
Multi-endpoint routing and extra agents are excluded. Existing generated files,
manifest hashes, historical reviews and unrelated execution logic remain unchanged.

## User flow

1. Open a saved task and select **Open in Databricks**. The app creates a unique
   temporary notebook in the signed-in user's ORION-notebooks/edits directory.
   Reopening an unfinished session reuses the attachment without overwriting edits.
2. Finish editing and stop editing that temporary notebook. Select **Preview returned
   edits** in the app. The app checks the saved session, workspace, user and exact
   object ID, exports Jupyter JSON, and shows a cell-numbered source diff.
3. Select **Save returned edits and delete temporary notebook**. The app rechecks
   the remote checksum against the preview and the app's current notebook revision.
   It saves an unapproved append-only revision associated with the same task file.
4. Only after the save response is acknowledged does the app request cleanup. The
   cleanup request verifies saved bytes and rechecks the remote object/checksum.
   It deletes only the recorded temporary notebook, never a folder or other file.
5. The app refreshes its saved code view and downloads to show the returned revision.
   Original generated notebooks and native Jobs YAML remain immutable. Deployment
   of the old candidate under an edited-code selection is blocked until a fresh
   reviewed candidate/parameter contract exists.

## Safety and recovery

- Failed saves never initiate cleanup. Failed/unconfirmed cleanup leaves an explicit
  retry control. Reopening the session after reload recovers that cleanup state.
- Missing remote objects can reconcile a lost deletion acknowledgement only after
  the corresponding saved revision and its checksum are verified.
- New remote edits after preview or save, changed object IDs, changed users/workspaces,
  invalid notebook JSON/syntax and credential literals block import or deletion.
- Execution outputs/counts are not imported. Cell source and existing metadata IDs
  are retained, with required language metadata/stable IDs assigned to new cells.
- No notebook execution, Jobs submission, email delivery or business-table mutation
  is needed for this flow.
- Databricks Workspace APIs do not offer conditional checksum deletion. The app
  checks immediately before delete, but cannot provide a distributed atomic lock
  against a concurrent editor writing after that check. Finish editing before return.
- Attachments predating this feature have no edit session receipt and are not
  automatically deleted or claimed as app-owned cleanup targets.

## Hosted persistence

The existing hosted dispatcher restores committed Delta metadata and immutable
Volume artifacts, flushes saves before acknowledging them, and uses fresh OBO
identity on cleanup. A failed or unconfirmed Delta publication cannot trigger the
client's next cleanup request. SQLite remains only the reconstructed ephemeral
execution cache. No local profile fallback is allowed in hosted edit operations.

Edit state uses the existing state namespace and existing settings/artifact_manifest
mapping. An additive mapping preserves the already-existing design tombstone table
in proposal_versions; its deletion behavior was not changed.

## Verification

Offline unit cases cover immutable originals, exact object deletion, save-first
ordering, identity/workspace/object/checksum conflicts, retry and credential/syntax
rejection. Actual browser-script cases cover success ordering and a failed durable
save without cleanup. A real DeltaRuntime mapper/restore test with fake cloud
transport checks revision durability across restart. These are not live hosted
OBO or end-to-end business parity certificates. Release results are tracked in
[current verification ledger](current-release-verification-ledger.md).
