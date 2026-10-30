"""Startup replaces only the current log and retains measurement evidence."""
import contextlib,importlib.util,io
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ps'))
spec=importlib.util.spec_from_file_location('battery_log_launcher',ROOT/'scripts/prototype_start.py')
launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)

class CurrentRunLogTests(unittest.TestCase):
    def test_restart_truncates_only_current_log_and_preserves_journal_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);data=root/'data';data.mkdir()
            measurement=data/'summary.json';measurement.write_text('retain measurement')
            calibration=root/'prototype_calibration.json';calibration.write_text('retain calibration')
            original={p:p.read_bytes() for p in (measurement,calibration)}
            for message in ('old-run-message','new-run-message'):
                stdout=io.StringIO();stderr=io.StringIO()
                with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr),patch.object(launcher.atexit,'register'):
                    path=launcher.start_current_log(root)
                    log=sys.stdout.log
                    print(message,flush=True);print('stderr-'+message,file=sys.stderr,flush=True)
                log.close()
                self.assertIn(message,stdout.getvalue());self.assertIn('stderr-'+message,stderr.getvalue())
            text=path.read_text(encoding='utf-8')
            self.assertNotIn('old-run-message',text);self.assertIn('new-run-message',text)
            self.assertEqual(text.count('RUN_START'),1)
            for p,contents in original.items():self.assertEqual(p.read_bytes(),contents)

if __name__=='__main__':unittest.main(verbosity=2)
