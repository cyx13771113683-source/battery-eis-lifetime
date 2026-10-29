import copy
from dataclasses import replace
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ps'))
from configuration import Settings,DEFAULT_SWEEP,digest
from calibration import Calibration,from_reference
from session import MeasurementJob
from session_audit import audit_session
from synthetic import SyntheticHardware
from model_bridge import HealthModel
from test_battery_eis import profile,reference
from test_lowfreq_model import dual_calibration


class SessionAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base=tempfile.TemporaryDirectory()
        p=profile()
        job=MeasurementJob(SyntheticHardware(),p,Settings(),DEFAULT_SWEEP,cls.base.name,
            calibration=dual_calibration(p),source='simulation')
        job.run()
        if job.snapshot()['status']!='DONE':raise AssertionError(job.snapshot())
        cls.session=Path(job.snapshot()['output'])

    @classmethod
    def tearDownClass(cls):cls.base.cleanup()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'session'
        shutil.copytree(self.session,self.path)

    def tearDown(self):self.tmp.cleanup()

    def change(self,name,modify):
        path=self.path/name
        data=json.loads(path.read_text());modify(data)
        path.write_text(json.dumps(data),encoding='utf-8')

    def test_complete_raw_reconstruction_has_no_hardware_claim(self):
        result=audit_session(self.path)
        self.assertTrue(result['data_integrity_ok'])
        self.assertTrue(result['recorded_measurement_valid'])
        self.assertFalse(result['eligible_for_hardware_dataset_review'])
        self.assertFalse(result['physical_accuracy_validated'])
        self.assertEqual(result['results'],4)
        self.assertEqual(len(result['captures']),5)
        self.assertTrue(all(r['all_18_moments_match'] for r in result['captures']))

    def test_a_single_raw_code_change_is_rejected(self):
        path=self.path/'001_raw.csv';rows=path.read_text().splitlines()
        fields=rows[2].split(',');fields[3]=str(int(fields[3])+1);rows[2]=','.join(fields)
        path.write_text('\n'.join(rows),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'PL moments'):audit_session(self.path)

    def test_saved_result_cannot_disagree_with_raw_data(self):
        self.change('summary.json',lambda d:d['results'][0].update(impedance_ohm=[.01,0]))
        with self.assertRaisesRegex(AssertionError,'reconstruction'):audit_session(self.path)

    def test_calibration_snapshot_change_and_external_mismatch_rejected(self):
        saved=self.path/'calibration_used.json'
        self.change(saved.name,lambda d:d['document']['rows'][0].update(voltage_correction=[1.01,0]))
        with self.assertRaisesRegex(ValueError,'snapshot hash'):audit_session(self.path)
        shutil.copy2(self.session/saved.name,saved)
        other=dual_calibration(profile()).document
        other['id']='another-reference'
        external=Path(self.tmp.name)/'other.json';external.write_text(json.dumps(other))
        with self.assertRaisesRegex(ValueError,'differs'):audit_session(self.path,external)

    def test_legacy_session_needs_explicit_matching_calibration(self):
        data=json.loads((self.path/'calibration_used.json').read_text())
        (self.path/'calibration_used.json').unlink()
        with self.assertRaisesRegex(ValueError,'original calibration'):audit_session(self.path)
        external=Path(self.tmp.name)/'legacy.json';external.write_text(json.dumps(data['document']))
        self.assertTrue(audit_session(self.path,external)['data_integrity_ok'])

    def test_duplicate_capture_and_unsafe_file_name_rejected(self):
        self.change('summary.json',lambda d:d['captures'].append(d['captures'][1]))
        with self.assertRaisesRegex(ValueError,'duplicate capture'):audit_session(self.path)
        shutil.copy2(self.session/'summary.json',self.path/'summary.json')
        self.change('summary.json',lambda d:d['captures'].__setitem__(1,'../001'))
        with self.assertRaisesRegex(ValueError,'capture list'):audit_session(self.path)

    def test_simulation_reference_marker_survives_and_blocks_hardware(self):
        ref=reference();ref['simulation_only']=True
        converted=from_reference(ref)
        self.assertTrue(converted['simulation_only'])
        self.assertEqual(converted['reference_document_sha256'],digest(ref))
        calibration=Calibration(converted)
        converted['rows'][0]['voltage_correction']=[1.5,0]
        self.assertNotEqual(calibration.document['rows'][0]['voltage_correction'],[1.5,0])
        hardware=SyntheticHardware()
        with self.assertRaisesRegex(ValueError,'simulation-only'):
            MeasurementJob(hardware,profile(),Settings(),[80],Path(self.tmp.name)/'never_created',
                calibration=calibration,source='hardware')
        self.assertEqual(hardware.sequence,0)
        self.assertFalse((Path(self.tmp.name)/'never_created').exists())
        for value in ('true',1,None):
            ref['simulation_only']=value
            with self.assertRaisesRegex(ValueError,'JSON boolean'):from_reference(ref)
            converted['simulation_only']=value
            with self.assertRaisesRegex(ValueError,'JSON boolean'):Calibration(converted)

    def test_source_and_stale_sequence_mismatch_rejected(self):
        self.change('002_capture.json',lambda d:d.update(source='hardware'))
        with self.assertRaisesRegex(ValueError,'source/name'):audit_session(self.path)
        shutil.copy2(self.session/'002_capture.json',self.path/'002_capture.json')
        self.change('002_capture.json',lambda d:d.update(sequence=1))
        with self.assertRaisesRegex(ValueError,'sequence'):audit_session(self.path)


class TerminalStateTests(unittest.TestCase):
    def make_job(self,folder,model=None):
        return MeasurementJob(SyntheticHardware(),profile(),Settings(),[80],folder,
            source='simulation',model=model)

    def test_final_summary_write_failure_reports_error_after_stop(self):
        model=HealthModel(ROOT/'models/frozen_two_frequency_20261022/lib_two_ridge.json')
        with tempfile.TemporaryDirectory() as folder:
            job=self.make_job(folder,model)
            with patch.object(job.recorder,'finish',side_effect=OSError('disk full')):
                job.run()
            self.assertEqual(job.snapshot()['status'],'ERROR')
            self.assertIn('record finalization failed',job.snapshot()['error'])
            self.assertEqual(job.snapshot()['ai']['reason'],'session_incomplete')
            self.assertFalse(job.hardware.active)

    def test_cancel_during_last_inference_cannot_finish_done(self):
        model=HealthModel(ROOT/'models/frozen_two_frequency_20261022/lib_two_ridge.json')
        with tempfile.TemporaryDirectory() as folder:
            job=self.make_job(folder,model)
            def late_cancel(*args):
                job.cancel()
                return {'status':'research_estimate','soh':.9}
            with patch.object(model,'evaluate',side_effect=late_cancel):job.run()
            self.assertEqual(job.snapshot()['status'],'CANCELLED')
            self.assertIsNone(job.snapshot()['ai']['soh'])
            self.assertFalse(job.hardware.active)
            self.assertEqual(json.loads((job.recorder.path/'summary.json').read_text())['status'],'CANCELLED')

    def test_cancel_after_completed_job_does_not_restore_busy_state(self):
        with tempfile.TemporaryDirectory() as folder:
            job=self.make_job(folder);job.run();before=job.snapshot()['status']
            self.assertEqual(before,'UNVERIFIED')
            job.cancel()
            self.assertEqual(job.snapshot()['status'],before)

    def test_cancel_after_terminal_commit_does_not_disagree_with_saved_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            job=self.make_job(folder);finish=job.recorder.finish
            def cancel_during_save(*args,**kwargs):
                job.cancel()
                self.assertNotEqual(job.snapshot()['status'],'CANCELLING')
                return finish(*args,**kwargs)
            with patch.object(job.recorder,'finish',side_effect=cancel_during_save):job.run()
            saved=json.loads((job.recorder.path/'summary.json').read_text())
            self.assertEqual(job.snapshot()['status'],'UNVERIFIED')
            self.assertEqual(saved['status'],job.snapshot()['status'])


if __name__=='__main__':unittest.main(verbosity=2)
