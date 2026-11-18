"""Invocation-local storage probe. Never open or mutate existing draft files."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from workbench.databricks_models import safe_failure_diagnostic


def check_writer_storage(directory):
    directory = Path(directory)
    operation = 'writer_preflight_temp_directory'
    receipt = {'status': 'running', 'checks': []}
    try:
        with TemporaryDirectory(prefix='writer-preflight-', dir=directory) as scratch:
            scratch = Path(scratch)
            operation = 'writer_preflight_write'
            original = scratch / 'probe.txt'
            with original.open('xb') as handle:
                handle.write(b'orion-writer-preflight\n')
            receipt['checks'].append('exclusive_write')
            operation = 'writer_preflight_read'
            if original.read_bytes() != b'orion-writer-preflight\n':
                raise ValueError('Storage probe bytes differ')
            receipt['checks'].append('read_back')
            operation = 'writer_preflight_rename'
            renamed = scratch / 'renamed.txt'
            original.rename(renamed)
            if renamed.read_bytes() != b'orion-writer-preflight\n':
                raise ValueError('Renamed storage probe bytes differ')
            receipt['checks'].append('rename_read_back')
            operation = 'writer_preflight_sqlite'
            with closing(sqlite3.connect(scratch / 'probe.sqlite')) as connection:
                connection.execute('CREATE TABLE probe (value TEXT NOT NULL)')
                connection.execute('INSERT INTO probe VALUES (?)', ('verified',))
                connection.commit()
                if (connection.execute('SELECT value FROM probe').fetchone() != ('verified',)
                        or connection.execute('PRAGMA integrity_check').fetchone() != ('ok',)):
                    raise ValueError('SQLite storage probe differs')
            receipt['checks'].append('sqlite_create_insert_commit_integrity')
            operation = 'writer_preflight_cleanup'
        receipt.update(status='passed', scratch_cleaned=True)
    except Exception as error:
        receipt.update(status='failed', failure_diagnostic=safe_failure_diagnostic(error, operation))
        try:
            with (directory / 'storage-preflight.json').open('x', encoding='utf-8') as handle:
                json.dump(receipt, handle, sort_keys=True)
        except OSError:
            pass  # The worker separately retains the live sanitized exception.
        raise
    # Successful checks are retained in the writer manifest, not an extra artifact.
    return receipt