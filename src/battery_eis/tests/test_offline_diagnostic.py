import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'ps'),str(ROOT/'scripts')]
from benchmark_ps import fixture
from configuration import Settings,DEFAULT_SWEEP
from session import MeasurementJob
from synthetic import SyntheticHardware
from diagnose_session import diagnose_session
from calibration import Calibration
from analysis import complex_value


class OfflineDiagnosticTests(unittest.TestCase):
    def test_complex_transfer_factors_are_compared_without_double_current_scaling(self):
        with tempfile.TemporaryDirectory() as out:
            profile,_,cal=fixture()
            document=cal.document
            for row in document['rows']:
                row['voltage_correction']=[1.02,.01];row['current_correction']=[.98,-.005]
            jobs=[]
            for index,calibration in enumerate((None,Calibration(document))):
                job=MeasurementJob(SyntheticHardware(),profile,Settings(),DEFAULT_SWEEP,Path(out)/str(index),
                    calibration=calibration,source='simulation');job.run();jobs.append(job)
            reports=[diagnose_session(j.recorder.path) for j in jobs]
            expected=complex(1.02,.01)/complex(.98,-.005)
            for plain,corrected in zip(reports[0]['windows'],reports[1]['windows']):
                ratio=complex_value(corrected['diagnostic_impedance_ohm'])/complex_value(plain['diagnostic_impedance_ohm'])
                self.assertAlmostEqual(abs(ratio-expected),0.,places=10)

    def test_calibrated_comparison_does_not_change_session_or_become_model_input(self):
        with tempfile.TemporaryDirectory() as out:
            profile,_,cal=fixture()
            job=MeasurementJob(SyntheticHardware(),profile,Settings(),DEFAULT_SWEEP,out,
                calibration=cal,source='simulation');job.run()
            path=job.recorder.path
            before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in path.iterdir()}
            report=diagnose_session(path)
            self.assertEqual(len(report['windows']),4)
            self.assertTrue(report['audit']['recorded_measurement_valid'])
            self.assertFalse(report['diagnostic_valid_for_measurement'])
            self.assertFalse(report['model_evaluation_allowed'])
            for row,saved in zip(report['windows'],job.results):
                self.assertEqual(row['production_impedance_ohm'],saved['impedance_ohm'])
                self.assertFalse(row['diagnostic_valid_for_measurement'])
                self.assertLess(row['relative_change_percent'],1.)
            after={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in path.iterdir()}
            self.assertEqual(before,after)

    def test_uncalibrated_session_keeps_research_status(self):
        with tempfile.TemporaryDirectory() as out:
            profile,_,_=fixture()
            job=MeasurementJob(SyntheticHardware(),profile,Settings(),[80],out,source='simulation');job.run()
            report=diagnose_session(job.recorder.path)
            self.assertFalse(report['audit']['recorded_measurement_valid'])
            self.assertEqual(len(report['windows']),2)
            self.assertFalse(report['diagnostic_valid_for_measurement'])

    def test_inconsistent_raw_data_cannot_produce_diagnostic(self):
        with tempfile.TemporaryDirectory() as out:
            profile,_,_=fixture()
            job=MeasurementJob(SyntheticHardware(),profile,Settings(),[80],out,source='simulation');job.run()
            p=job.recorder.path/'001_capture.json'
            data=json.loads(p.read_text());data['moments'][0][1]+=1;p.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'PL moments'):diagnose_session(job.recorder.path)
