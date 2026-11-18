"""Focused storage probe checks, no model or network."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from workbench.writer_preflight import check_writer_storage


def main():
    with TemporaryDirectory() as root:
        root = Path(root)
        existing = root / 'existing.txt'
        existing.write_bytes(b'unchanged')
        result = check_writer_storage(root)
        assert result['status'] == 'passed' and len(result['checks']) == 4
        assert result['scratch_cleaned'] is True
        assert existing.read_bytes() == b'unchanged'
        assert sorted(path.name for path in root.iterdir()) == ['existing.txt']
    with TemporaryDirectory() as root:
        root = Path(root)
        with patch('workbench.writer_preflight.sqlite3.connect', side_effect=PermissionError(13, 'private')):
            try:
                check_writer_storage(root)
            except PermissionError:
                pass
            else:
                raise AssertionError('Permission failure must stop before inference')
        receipt = json.loads((root / 'storage-preflight.json').read_text())
        assert receipt['status'] == 'failed'
        assert receipt['failure_diagnostic']['operation'] == 'writer_preflight_sqlite'
        assert 'private' not in json.dumps(receipt)
        assert not list(root.glob('writer-preflight-*'))
    print('WRITER_STORAGE_PREFLIGHT=PASS')


if __name__ == '__main__':
    main()