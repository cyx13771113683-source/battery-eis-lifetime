"""Prototype lifecycle: queued start, cancellation and summary publication."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_prototype import ReplayJob,TestHardware,pending_profile,calibration,model
from configuration import Settings


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='eis-lifecycle-');self.addCleanup(self.temp.cleanup)
        self.hardware=TestHardware()
        self.job=ReplayJob(self.hardware,pending_profile(),Settings(),[80.,17.8,2.16],self.temp.name,
                           calibration(),source='simulation',model=model())

    def summary(self):return json.loads((self.job.recorder.path/'summary.json').read_text())

    def test_start_marks_busy_before_worker_is_scheduled(self):
        class QueuedThread:
            def __init__(self,**kwargs):pass
            def start(self):pass
        with patch('prototype.threading.Thread',QueuedThread):self.job.start()
        self.assertEqual(self.job.snapshot()['status'],'INITIALIZING')
        with self.assertRaisesRegex(RuntimeError,'already started'):self.job.start()

    def test_cancel_before_worker_runs_does_not_initialize_adc(self):
        self.job.cancel()
        with patch.object(self.hardware,'initialize',wraps=self.hardware.initialize) as initialize:
            self.job.run()
        initialize.assert_not_called()
        self.assertEqual(self.job.snapshot()['status'],'CANCELLED')
        self.assertIsNone(self.summary()['ai']['soh'])

    def test_cancel_during_summary_does_not_claim_to_cancel_a_committed_result(self):
        original=self.job.recorder.finish
        observed=[]
        def finish(*args,**kwargs):
            self.job.cancel();observed.append(self.job.cancel_event.is_set())
            return original(*args,**kwargs)
        with patch.object(self.job.recorder,'finish',side_effect=finish):self.job.run()
        self.assertEqual(observed,[False],'summary finalization has passed the cancellation boundary')
        self.assertEqual(self.job.snapshot()['status'],'DONE')
        self.assertEqual(self.summary()['status'],'DONE')

    def test_cancel_after_last_capture_before_model_finishes_clears_soh(self):
        original=self.job.model.evaluate
        def evaluate(*args,**kwargs):
            ai=original(*args,**kwargs);self.job.cancel();return ai
        with patch.object(self.job.model,'evaluate',side_effect=evaluate):self.job.run()
        self.assertEqual(self.job.snapshot()['status'],'CANCELLED')
        self.assertIsNone(self.job.snapshot()['ai']['soh'])
        self.assertEqual(self.summary()['status'],'CANCELLED')

    def test_summary_commit_failure_has_no_success_or_soh(self):
        with patch.object(self.job.recorder,'commit_summary',side_effect=OSError('SD write failed')):
            self.job.run()
        self.assertEqual(self.job.snapshot()['status'],'ERROR')
        self.assertIsNone(self.job.snapshot()['ai']['soh'])
        self.assertFalse((self.job.recorder.path/'summary.json').exists())

    def test_commit_overrun_demotes_saved_summary(self):
        clock=[0.];original=self.job.recorder.commit_summary
        def commit(summary):
            value=original(summary)
            if summary['status']=='DONE':clock[0]=21.
            return value
        with patch('prototype.time.monotonic',side_effect=lambda:clock[0]),\
             patch.object(self.job.recorder,'commit_summary',side_effect=commit):
            self.job.run()
        self.assertEqual(self.job.snapshot()['status'],'TIMEOUT')
        self.assertIsNone(self.summary()['ai']['soh'])
        self.assertEqual(self.summary()['status'],'TIMEOUT')
        self.assertTrue((self.job.recorder.path/'summary.before_overrun.json').is_file())


if __name__=='__main__':unittest.main(verbosity=2)
