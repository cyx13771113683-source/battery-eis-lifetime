"""Current tare, history reader and reviewed PS proposals must work together."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ps'))
from configuration import Settings
from current_zero import CurrentZeroReference
from session import MeasurementJob
from session_audit import audit_session
from ui_records import RecordIndex
from test_current_zero import OffsetHardware, reference_document
from test_finalization_deadline import FakeClock


class ReviewIntegrationTests(unittest.TestCase):
    def job(self, directory):
        from test_battery_eis import profile
        return MeasurementJob(OffsetHardware(), profile(), Settings(), [80], directory,
            source='simulation', current_zero=CurrentZeroReference(reference_document()))

    def test_new_diagnostic_and_tare_reconstruct_together(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory); job.run()
            self.assertEqual(job.snapshot()['status'], 'UNVERIFIED')
            out = audit_session(job.recorder.path)
            self.assertTrue(out['data_integrity_ok'])
            for row in job.results:
                self.assertIn('current_dc_corrected_a', row)
                self.assertTrue(all('residual_lag1_correlation' in d for d in row['diagnostics']))

    def test_altered_new_diagnostic_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory); job.run()
            path = job.recorder.path / 'summary.json'
            data = json.loads(path.read_text(encoding='utf-8'))
            data['results'][0]['diagnostics'][0]['residual_lag1_correlation'] += .1
            path.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaisesRegex(AssertionError, 'residual correlation'):
                audit_session(job.recorder.path)

    def test_old_diagnostics_without_new_field_remain_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory); job.run()
            paths = [job.recorder.path / 'summary.json'] + list(job.recorder.path.glob('*_result.json'))
            for path in paths:
                data = json.loads(path.read_text(encoding='utf-8'))
                for row in data.get('results', [data]):
                    for diagnostic in row.get('diagnostics', []):
                        diagnostic.pop('residual_lag1_correlation', None)
                path.write_text(json.dumps(data), encoding='utf-8')
            self.assertTrue(audit_session(job.recorder.path)['data_integrity_ok'])

    def test_tare_does_not_bypass_deadline_and_history_shows_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory); clock = FakeClock()
            original = job.recorder.finish
            def slow_finish(*args, **kwargs):
                clock.now += 21.
                return original(*args, **kwargs)
            with patch('session.time.monotonic', clock.monotonic), \
                 patch('session.time.sleep', clock.sleep), \
                 patch.object(job.recorder, 'finish', side_effect=slow_finish):
                job.run()
            self.assertEqual(job.snapshot()['status'], 'TIMEOUT')
            record = RecordIndex(directory).page(0)[0]
            self.assertEqual(record['status'], 'TIMEOUT')
            self.assertFalse(record['measurement_valid'])
            self.assertTrue((job.recorder.path / 'summary.staged.json').exists())
            self.assertEqual(len(RecordIndex(directory).entries), 1)
