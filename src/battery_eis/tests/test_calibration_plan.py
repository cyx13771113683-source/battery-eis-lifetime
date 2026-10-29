from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ps'))
from configuration import HardwareProfile, Settings
from calibration import Calibration, from_reference
from calibration_plan import reference_template, calibration_coverage, CALIBRATION_FREQUENCIES
from session import MeasurementJob


def profile():
    return replace(HardwareProfile.load(ROOT / 'config/profile.example.json'),
                   wiring_verified=True, module_verified=True, adc_clock_verified=True,
                   polarity_verified=True, limits_confirmed=True)


def measured_reference(frequencies=(10., 20., 40., 50., 80., 100.)):
    document = reference_template(profile(), frequencies=frequencies)
    document.update(id='synthetic-reference-test-only', evidence_kind='independent_reference',
                    reference_source='unit test fixture, not a physical calibration')
    for index, row in enumerate(document['rows']):
        row.update(measured_voltage_ac_v=[.001, 0], measured_current_ac_a=[.01, 0],
                   reference_voltage_ac_v=[.001 * (1 + .01 * index), .00001 * index],
                   reference_current_ac_a=[.01, -.0001 * index], voltage_range_v=[3., 4.2],
                   current_ac_range_a=[.001, .4], reference_uncertainty_relative=.005)
    return document


class FrequencyCalibrationTests(unittest.TestCase):
    def test_full_band_is_planning_not_acquisition_or_coefficients(self):
        document = reference_template(profile())
        self.assertEqual([row['requested_frequency_hz'] for row in document['rows']], list(CALIBRATION_FREQUENCIES))
        supported = [row['requested_frequency_hz'] for row in document['rows'] if row['acquisition_supported']]
        self.assertEqual(supported, [2., 5., 10., 20., 40., 50., 80., 100.])
        for row in document['rows']:
            self.assertIsNone(row['reference_voltage_ac_v'])
            if not row['acquisition_supported']:
                self.assertIsNone(row['binding'])
                self.assertIsNone(row['actual_frequency_hz'])
                self.assertTrue(row['blocked_reason'])
        self.assertEqual(document['rows'][0]['minimum_capture_s_at_requested_frequency'], 8)
        with self.assertRaisesRegex(ValueError, 'independent reference'):
            from_reference(document)
        with self.assertRaises(ValueError):
            Calibration(document)

    def test_blank_and_unsupported_rows_cannot_be_promoted(self):
        document = reference_template(profile(), frequencies=[80.])
        document.update(id='test', evidence_kind='independent_reference', reference_source='test')
        with self.assertRaisesRegex(ValueError, 'incomplete independent reference'):
            from_reference(document)
        unsupported = measured_reference([10000.])
        with self.assertRaisesRegex(ValueError, 'unsupported acquisition'):
            from_reference(unsupported)

    def test_each_frequency_uses_its_own_complex_corrections(self):
        reference = measured_reference()
        calibration = Calibration(from_reference(reference))
        for index, row in enumerate(reference['rows']):
            (cv, ci), _ = calibration.corrections(row['binding'], 3.7, .02)
            self.assertAlmostEqual(cv, complex(1 + .01 * index, .01 * index))
            self.assertAlmostEqual(ci, complex(1., -.01 * index))
        report = calibration_coverage(calibration, profile(), Settings(), [10, 20, 40, 50, 80, 100])
        self.assertTrue(report['complete'])
        self.assertTrue(all(row['status'] == 'matched' for row in report['rows']))

    def test_missing_and_out_of_band_points_reported_without_interpolation(self):
        calibration = Calibration(from_reference(measured_reference([80.])))
        report = calibration_coverage(calibration, profile(), Settings(), [1, 10, 79, 80, 100, 10000])
        self.assertFalse(report['complete'])
        self.assertEqual([row['status'] for row in report['rows']],
                         ['unsupported_acquisition', 'missing_calibration', 'missing_calibration',
                          'matched', 'missing_calibration', 'unsupported_acquisition'])
        changed = calibration_coverage(calibration, replace(profile(), board_serial='changed'), Settings(), [80])
        self.assertEqual(changed['rows'][0]['status'], 'missing_calibration')

    def test_all_missing_points_rejected_before_capture_or_session_creation(self):
        calibration = Calibration(from_reference(measured_reference([80.])))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, '10.0, 100.0'):
                MeasurementJob(object(), profile(), Settings(), [10., 80., 100.], directory, calibration)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_grid_validation_and_firmware_limits_preserved(self):
        for frequencies in ([], [80] * 21, [80, 80], [0], [10001], [float('nan')], [True]):
            with self.assertRaises(ValueError):
                reference_template(profile(), frequencies=frequencies)
        for frequency in (1., 1000., 10000.):
            with self.assertRaises(ValueError):
                Settings(frequency_hz=frequency).validate()

    def test_cli_template_and_coverage_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            template = directory / 'template.json'
            base = [sys.executable, '-B', str(ROOT / 'ps/app.py')]
            profile_args = ['--profile', str(ROOT / 'config/profile.example.json')]
            result = subprocess.run(base + ['calibration-template'] + profile_args + ['--output', str(template)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(json.loads(template.read_text())['rows']), 17)
            calibration_path = directory / 'transfer.json'
            calibration_path.write_text(json.dumps(from_reference(measured_reference([80.]))), encoding='utf-8')
            for points, expected in ((['80'], 0), (['80', '10000'], 2)):
                output = directory / ('coverage%d.json' % expected)
                result = subprocess.run(base + ['calibration-check'] + profile_args +
                                        ['--calibration', str(calibration_path), '--sweep'] + points +
                                        ['--output', str(output)], capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(json.loads(output.read_text())['complete'], expected == 0)


if __name__ == '__main__':
    unittest.main()
