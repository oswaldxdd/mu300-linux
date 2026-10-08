"""Execute the actual delegate PM helper/command handler with stub PM APIs."""
import pathlib
import shutil
import subprocess
import tempfile
import unittest

TOP = pathlib.Path(__file__).resolve().parents[1]


class SipaRuntimePm(unittest.TestCase):
    def test_command_and_reference_lifetime(self):
        cc = shutil.which('cc') or shutil.which('gcc')
        if not cc:
            self.skipTest('a host C compiler is required')
        source = (TOP / 'upstream/modules/sprd_modem/sipa_delegate/sipa_dele_cp_in_ap.c').read_text()
        begin = source.index('static int cp_dele_get_pd(')
        end = source.index('static int cp_dele_local_req_r_prod(')
        with tempfile.TemporaryDirectory(prefix='mu300-sipa-pm-') as d:
            work = pathlib.Path(d)
            (work / 'sipa_pm_under_test.h').write_text(source[begin:end])
            binary = work / 'test-pm'
            built = subprocess.run([cc, '-std=c99', '-Wall', '-Wextra', '-Werror',
                                    '-I', str(work), str(TOP / 'tests/sipa_pm_harness.c'),
                                    '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=5)
            self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == '__main__':
    unittest.main()
