"""Invalid requests must never reach the overlay loader or create data folders."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ps'))
import app
from session import MeasurementJob
from configuration import Settings
from synthetic import SyntheticHardware
from test_battery_eis import profile
from test_lowfreq_model import dual_calibration
from test_current_zero import reference_document


class RequestPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        folder=Path(self.tmp.name)
        p=folder/'profile.json';p.write_text(json.dumps(asdict(profile())))
        self.args=argparse.Namespace(command='measure',profile=p,overlay=folder/'never.bit',
            calibration=None,output=folder/'no_data',frequency=None,sweep=None,repeats=2,
            model=ROOT/'models/frozen_two_frequency_20261022/lib_two_ridge.json',
            budget=20.,metadata=None,position='normal',pair_id=None,confirm_zero_before_move=False)

    def tearDown(self):self.tmp.cleanup()

    def rejected_before_load(self,pattern):
        with patch.object(app,'parse',return_value=self.args),patch('hardware.BatteryHardware.load') as loader:
            with self.assertRaisesRegex(ValueError,pattern):app.main()
            loader.assert_not_called()
        self.assertFalse(self.args.output.exists())

    def test_invalid_budget_and_frequency_do_not_load_overlay(self):
        self.args.budget=.01;self.rejected_before_load('time budget')
        self.args.budget=20.;self.args.sweep=[1.];self.rejected_before_load('frequency')

    def test_simulation_calibration_does_not_load_overlay(self):
        cal=dual_calibration(profile()).document;cal['simulation_only']=True
        self.args.calibration=Path(self.tmp.name)/'calibration.json'
        self.args.calibration.write_text(json.dumps(cal))
        self.rejected_before_load('simulation-only')

    def test_simulation_current_zero_reference_does_not_load_overlay(self):
        self.args.current_zero=Path(self.tmp.name)/'zero.json'
        self.args.current_zero.write_text(json.dumps(reference_document()),encoding='utf-8')
        self.rejected_before_load('source')

    def test_nonfinite_metadata_is_rejected_before_file_or_hardware_changes(self):
        self.args.metadata=Path(self.tmp.name)/'metadata.json'
        self.args.metadata.write_text('{"temperature_c":NaN}')
        self.rejected_before_load('finite number')
        for value in (float('nan'),float('inf'),True):
            with self.assertRaisesRegex(ValueError,'finite number'):
                MeasurementJob(SyntheticHardware(),profile(),Settings(),[80],self.args.output,
                    source='simulation',metadata={'capacity_ah':value})
            self.assertFalse(self.args.output.exists())

    def test_valid_request_reaches_only_mock_loader(self):
        with patch.object(app,'parse',return_value=self.args),patch('hardware.BatteryHardware.load',side_effect=RuntimeError('mock loader reached')) as loader:
            with self.assertRaisesRegex(RuntimeError,'mock loader reached'):app.main()
            loader.assert_called_once()
        self.assertFalse(self.args.output.exists())


if __name__=='__main__':unittest.main(verbosity=2)
