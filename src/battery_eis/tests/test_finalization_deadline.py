"""Final-summary deadline regressions, fake clock and synthetic backend only."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ps'))
from configuration import Settings,DEFAULT_SWEEP
from session import MeasurementJob
from synthetic import SyntheticHardware
from test_battery_eis import profile
from test_lowfreq_model import dual_calibration


class FakeClock:
    def __init__(self):self.now=1000.
    def monotonic(self):return self.now
    def sleep(self,seconds):self.now+=seconds


class ToyModel:
    def evaluate(self,*args):return dict(status='research_estimate',soh=.9,rul=None,valid_for_board=False)
    def reject(self,reason):return dict(status='rejected',reason=reason,soh=None,rul=None,valid_for_board=False)


class FinalizationDeadlineTests(unittest.TestCase):
    def execute(self,root,calibrated=True,cost=0.,model=None,fail_revision=False,stop_error=False,cancel=False,boundary=False,commit_cost=0.,deny_rename=False):
        p=profile();clock=FakeClock()
        job=MeasurementJob(SyntheticHardware(),p,Settings(),DEFAULT_SWEEP,root,
             calibration=dual_calibration(p) if calibrated else None,source='simulation',model=model,budget_s=20.)
        original=job.recorder.finish;commit=job.recorder.commit_summary;calls=0;commits=0;raw_before={}
        def save(*args,**kwargs):
            nonlocal calls,raw_before
            calls+=1
            if calls==1:
                raw_before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in job.recorder.path.glob('*_raw.csv')}
                if boundary:clock.now=1020.
                else:clock.now+=cost
                if cancel:job.cancel()
            return original(*args,**kwargs)
        def publish(summary):
            nonlocal commits
            commits+=1
            if fail_revision:raise OSError('injected publication write failure')
            result=commit(summary)
            if commits==1:clock.now+=commit_cost
            return result
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch('session.time.monotonic',clock.monotonic))
            stack.enter_context(patch('session.time.sleep',clock.sleep))
            stack.enter_context(patch.object(job.recorder,'finish',side_effect=save))
            stack.enter_context(patch.object(job.recorder,'commit_summary',side_effect=publish))
            if deny_rename:stack.enter_context(patch.object(Path,'rename',side_effect=PermissionError('injected rename denial')))
            if stop_error:
                with patch.object(job.hardware,'stop',side_effect=RuntimeError('injected stop failure')):job.run()
            else:job.run()
        self.assertEqual(raw_before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in job.recorder.path.glob('*_raw.csv')})
        self.assertFalse(job.hardware.active)
        saved=json.loads((job.recorder.path/'summary.json').read_text()) if (job.recorder.path/'summary.json').is_file() else None
        return job,saved,calls

    def test_fast_calibrated_completion_keeps_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            job,saved,calls=self.execute(tmp)
            self.assertEqual((job.snapshot()['status'],saved['status'],calls),('DONE','DONE',1))
            self.assertFalse(list(job.recorder.path.glob('summary.before_overrun*.json')))

    def test_slow_summary_cannot_keep_calibrated_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            job,saved,calls=self.execute(tmp,cost=21.)
            self.assertEqual((job.snapshot()['status'],saved['status'],calls),('TIMEOUT','TIMEOUT',1))
            self.assertGreaterEqual(saved['timing']['elapsed_after_primary_summary_s'],20.)
            prior=json.loads((job.recorder.path/'summary.staged.json').read_text())
            self.assertEqual(prior['status'],'DONE')

    def test_slow_summary_also_refuses_unverified_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            job,saved,_=self.execute(tmp,calibrated=False,cost=21.)
            self.assertEqual(job.snapshot()['status'],'TIMEOUT');self.assertEqual(saved['status'],'TIMEOUT')

    def test_late_model_estimate_is_cleared_in_ui_and_saved_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            job,saved,_=self.execute(tmp,cost=21.,model=ToyModel())
            for value in (job.snapshot()['ai'],saved['ai']):
                self.assertEqual(value['reason'],'session_incomplete');self.assertIsNone(value['soh'])

    def test_exact_budget_boundary_is_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            job,saved,_=self.execute(tmp,boundary=True)
            self.assertEqual((job.snapshot()['status'],saved['status']),('TIMEOUT','TIMEOUT'))

    def test_revision_failure_leaves_no_canonical_success_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            job,saved,_=self.execute(tmp,cost=21.,fail_revision=True,model=ToyModel())
            self.assertEqual(job.snapshot()['status'],'ERROR');self.assertIsNone(saved)
            self.assertIsNone(job.snapshot()['ai']['soh'])
            self.assertTrue((job.recorder.path/'summary.staged.json').is_file())

    def test_existing_stop_error_is_not_replaced_by_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            job,saved,calls=self.execute(tmp,cost=21.,stop_error=True)
            self.assertEqual((job.snapshot()['status'],saved['status'],calls),('ERROR','ERROR',1))
            self.assertIn('stop acknowledgement failed',saved['error'])

    def test_late_cancel_during_disk_commit_cannot_diverge_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            job,saved,_=self.execute(tmp,cost=21.,cancel=True)
            self.assertEqual((job.snapshot()['status'],saved['status']),('TIMEOUT','TIMEOUT'))

    def test_under_budget_model_output_is_not_changed_by_deadline_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            job,saved,calls=self.execute(tmp,model=ToyModel())
            self.assertEqual(job.snapshot()['status'],'DONE');self.assertEqual(saved['ai']['soh'],.9)
            self.assertEqual(calls,1);self.assertFalse(saved['ai']['valid_for_board'])

    def test_slow_atomic_publication_also_demotes_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            job,saved,_=self.execute(tmp,commit_cost=21.,model=ToyModel())
            self.assertEqual((job.snapshot()['status'],saved['status']),('TIMEOUT','TIMEOUT'))
            self.assertIsNone(saved['ai']['soh'])
            self.assertTrue(list(job.recorder.path.glob('summary.before_overrun*.json')))

    def test_denied_first_rename_leaves_no_canonical_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            job,saved,_=self.execute(tmp,cost=21.,deny_rename=True)
            self.assertEqual(job.snapshot()['status'],'ERROR');self.assertIsNone(saved)
            self.assertTrue((job.recorder.path/'summary.staged.json').is_file())


if __name__=='__main__':unittest.main()
