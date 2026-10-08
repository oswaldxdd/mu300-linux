"""Exercise the real restore helper's complete payload and failure cleanup."""
from pathlib import Path
import subprocess
import tempfile

here = Path(__file__).resolve().parent
library = here / 'runtime/fcc-record.sh'
with tempfile.TemporaryDirectory(prefix='fcc-restore-test-') as work:
    directory = Path(work)
    target = directory / 'attribute'
    def run(body, success=True):
        result = subprocess.run(['sh', '-c',
            '. "$1"; task_restore_directory=$3; mktemp() { command mktemp "$task_restore_directory/request.XXXXXX"; }; ' + body,
            'test', str(library), str(target), str(directory)],
            text=True, capture_output=True)
        assert (result.returncode == 0) == success, result.stderr
        assert not list(directory.glob('request.*')), 'restore temporary leaked'
    run('fcc_restore_write "$2" 3983 4050 661')
    assert target.read_bytes() == b'3983 4050 661\n'
    run('cat() { return 1; }; fcc_restore_write "$2" 3983 4050 661', False)
    run('mktemp() { return 1; }; fcc_restore_write "$2" 3983 4050 661', False)
    target.write_bytes(b'unchanged')
    run('fcc_restore_write "$2" 999 4050 661', False)
    assert target.read_bytes() == b'unchanged'
    run('chmod() { return 1; }; fcc_restore_write "$2" 3983 4050 661', False)
    assert target.read_bytes() == b'unchanged'
print('PASS: complete restore payload; invalid input and preparation failures do not write; failed transfer cleans temporary')
