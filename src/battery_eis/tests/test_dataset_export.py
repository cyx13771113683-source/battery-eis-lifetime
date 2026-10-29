"""Prevent label/source leakage when converting a capture into research inputs."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ps'))
from configuration import Settings,DEFAULT_SWEEP
from dataset_export import export_candidate
from session import MeasurementJob
from synthetic import SyntheticHardware
from test_battery_eis import profile
from test_lowfreq_model import dual_calibration


class DatasetExportTests(unittest.TestCase):
    def test_audited_export_preserves_source_units_sign_and_never_invents_labels(self):
        with tempfile.TemporaryDirectory() as folder:
            p=profile();job=MeasurementJob(SyntheticHardware(),p,Settings(),DEFAULT_SWEEP,folder,
                calibration=dual_calibration(p),source='simulation',
                metadata={'soc_fraction':.5,'temperature_c':20.,'capacity_ah':2.7,
                          'soh':.9,'source_domain':'LIB','aging_group':'group-1'})
            job.run();out=export_candidate(job.recorder.path)
            self.assertEqual(out['source'],'simulation')
            self.assertEqual(out['features']['source_domain'],'simulation')
            self.assertEqual(out['features']['soc_percent'],50.)
            self.assertEqual(out['features']['impedance_unit'],'ohm')
            self.assertTrue(all(v<0 for v in out['features']['z_imag_ohm']))
            self.assertTrue(all(s['repeats']==2 for s in out['spectra']))
            self.assertFalse(out['training_eligible'])
            self.assertTrue(all(v is None for v in out['labels'].values()))
            self.assertEqual(out['context_recorded']['capacity_ah'],2.7)
            self.assertEqual(out['split_group_cell_id'],p.cell_id)
            self.assertEqual(out['aging_group'],'group-1')
            self.assertEqual(export_candidate(job.recorder.path)['candidate_id'],out['candidate_id'])

    def test_unverified_capture_is_not_exported_as_valid_training_input(self):
        with tempfile.TemporaryDirectory() as folder:
            job=MeasurementJob(SyntheticHardware(),profile(),Settings(),DEFAULT_SWEEP,folder,source='simulation')
            job.run()
            with self.assertRaisesRegex(ValueError,'independent reconstruction'):export_candidate(job.recorder.path)

    def test_standard_and_regulated_source_do_not_become_battery_examples(self):
        with tempfile.TemporaryDirectory() as folder:
            p=profile();job=MeasurementJob(SyntheticHardware(),p,Settings(),DEFAULT_SWEEP,folder,
                calibration=dual_calibration(p),source='simulation',position='A')
            job.run()
            with self.assertRaisesRegex(ValueError,'A/B standards'):export_candidate(job.recorder.path)
            p=profile();job=MeasurementJob(SyntheticHardware(),p,Settings(),DEFAULT_SWEEP,folder,
                calibration=dual_calibration(p),source='simulation',metadata={'port_kind':'regulated_source'})
            job.run()
            with self.assertRaisesRegex(ValueError,'regulated sources'):export_candidate(job.recorder.path)


if __name__=='__main__':unittest.main(verbosity=2)
