"""Offline regressions for signed current zeroing and independent protection."""
from datetime import datetime, timezone, timedelta
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ps'))
from configuration import Settings
from analysis import analyze
from synthetic import SyntheticHardware, make_capture, moments_from_raw, sine_lut
from session import MeasurementJob
from session_audit import audit_session
from test_battery_eis import profile
from current_zero import CurrentZeroReference, evaluate_zero_drive


def preflight_settings():
    return replace(Settings(), frequency_hz=80., settle_s=.2, capture_s=.2)


def shifted_capture(offset_a=.0033, drive=False):
    p = profile()
    capture = make_capture(preflight_settings() if not drive else Settings(), p, drive=drive)
    mask = capture.raw[:, 3] == 1
    capture.raw[mask, 2] += round(offset_a * p.r_shunt_ohm / p.code_volts)
    capture.moments = moments_from_raw(capture.raw, sine_lut())
    return capture


def reference_document(offset_a=.0033):
    # Explicit simulation reference. This must never be accepted on hardware.
    now = datetime.now(timezone.utc)
    return dict(schema='battery-current-zero/1', id='test-only-zero', source='simulation',
        known_zero_verified=True, known_zero_evidence='independent fixture test; simulated',
        evidence_sha256='a' * 64, created_utc=now.isoformat(),
        expires_utc=(now + timedelta(hours=1)).isoformat(),
        binding=profile().binding(preflight_settings()), adc_config=0xf0000130,
        slot_cycles=32768, voltage_range_v=[3.6, 3.8], offset_a=offset_a,
        uncertainty_a=.00005)


class OffsetHardware(SyntheticHardware):
    def __init__(self, offset_a=.0033):
        super().__init__()
        self.offset_a = offset_a
        self.drives = []

    def start(self, settings, p, drive=True):
        self.drives.append(drive)
        super().start(settings, p, drive)
        mask = self.capture.raw[:, 3] == 1
        self.capture.raw[mask, 2] += round(self.offset_a * p.r_shunt_ohm / p.code_volts)
        self.capture.moments = moments_from_raw(self.capture.raw, sine_lut())


class CurrentZeroTests(unittest.TestCase):
    def test_constant_offset_does_not_change_ac_on_noninteger_cycle_window(self):
        settings = Settings(frequency_hz=17.8, capture_s=.65)
        baseline = make_capture(settings, profile())
        before = analyze(baseline, settings, profile())
        indices = baseline.raw[:, 3] == 1
        baseline.raw[indices, 2] += 550
        baseline.moments = moments_from_raw(baseline.raw, sine_lut())
        after = analyze(baseline, settings, profile())
        np.testing.assert_allclose(after['current_ac_a'], before['current_ac_a'], rtol=1e-12, atol=1e-12)
        np.testing.assert_allclose(after['impedance_ohm'], before['impedance_ohm'], rtol=1e-12, atol=1e-12)
        self.assertAlmostEqual(after['current_dc_a'] - before['current_dc_a'],
                               550 * profile().code_volts / profile().r_shunt_ohm, places=12)

    def test_unverified_live_mean_does_not_arm_itself(self):
        row = evaluate_zero_drive(shifted_capture(), profile())
        self.assertFalse(row['valid'])
        self.assertIn('absolute_zero_current_over_1mA', row['reasons'])
        self.assertEqual(row['offset_applied_a'], 0)

    def test_valid_reference_tares_both_signs_using_signed_samples(self):
        for offset in (.0033, -.0033):
            reference = CurrentZeroReference(reference_document(offset))
            row = evaluate_zero_drive(shifted_capture(offset), profile(), reference, 'simulation')
            self.assertTrue(row['valid'], row)
            self.assertAlmostEqual(row['offset_applied_a'], offset, delta=.00002)
            self.assertLess(row['residual_peak_a'], .0001)
            self.assertGreater(row['raw_abs_max_a'], .003)
            self.assertFalse(row['physical_leakage_excluded'])

    def test_real_added_current_cannot_be_zeroed_away(self):
        ref = CurrentZeroReference(reference_document())
        for offset in (.0043, .03):
            row = evaluate_zero_drive(shifted_capture(offset), profile(), ref, 'simulation')
            self.assertFalse(row['valid'])
            self.assertEqual(row['offset_applied_a'], 0)
        self.assertIn('raw_zero_current_over_10mA', row['reasons'])

    def test_noise_spike_drift_missing_channel_and_adc_saturation_fail(self):
        ref = CurrentZeroReference(reference_document())
        for kind in ('noise', 'spike', 'drift', 'missing', 'saturated', 'fault', 'bad_config', 'duplicate'):
            c = shifted_capture()
            indices = np.flatnonzero(c.raw[:, 3] == 1)
            if kind == 'noise': c.raw[indices, 2] += np.arange(len(indices)) % 2 * 250
            if kind == 'spike': c.raw[indices[0], 2] += 200
            if kind == 'drift': c.raw[indices, 2] += np.linspace(-90, 90, len(indices)).astype(int)
            if kind == 'missing': c.raw = c.raw[c.raw[:, 3] == 0]
            if kind == 'saturated': c.raw[indices[0], 2] = 8388607
            if kind == 'fault': c.meta['faults'] = 1
            if kind == 'bad_config': c.meta['adc_registers'][0] ^= 2
            if kind == 'duplicate': c.raw[1, 0] = c.raw[0, 0]
            c.moments = moments_from_raw(c.raw, sine_lut())
            with self.subTest(kind=kind):
                self.assertFalse(evaluate_zero_drive(c, profile(), ref, 'simulation')['valid'])

    def test_reference_cannot_change_identity_voltage_source_or_age(self):
        for field, value in [('known_zero_verified', False), ('offset_a', float('nan')),
                             ('offset_a', True), ('uncertainty_a', .001),
                             ('known_zero_evidence', ''), ('voltage_range_v', [0, 5])]:
            doc = reference_document(); doc[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): CurrentZeroReference(doc)
        ref = CurrentZeroReference(reference_document())
        with self.assertRaisesRegex(ValueError, 'source'):
            ref.validate_for(profile(), 'hardware')
        with self.assertRaisesRegex(ValueError, 'binding'):
            ref.validate_for(replace(profile(), board_serial='different'), 'simulation')
        with self.assertRaisesRegex(ValueError, 'expired'):
            ref.validate_for(profile(), 'simulation', datetime.now(timezone.utc) + timedelta(days=1))
        c = shifted_capture(); c.raw[c.raw[:, 3] == 0, 2] -= 300000
        c.moments = moments_from_raw(c.raw, sine_lut())
        self.assertFalse(evaluate_zero_drive(c, profile(), ref, 'simulation')['valid'])

    def test_job_no_reference_never_drives_and_records_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            h = OffsetHardware()
            job = MeasurementJob(h, profile(), Settings(), [80], folder, source='simulation')
            job.run()
            self.assertEqual(job.snapshot()['status'], 'ERROR')
            self.assertEqual(h.drives, [False])
            saved = json.loads((job.recorder.path / '000_result.json').read_text())
            self.assertFalse(saved['valid'])
            self.assertGreater(saved['raw_mean_a'], .003)
            self.assertFalse(h.active)

    def test_reference_job_reconstructs_and_keeps_ac_and_absolute_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            h = OffsetHardware()
            ref = CurrentZeroReference(reference_document())
            job = MeasurementJob(h, profile(), Settings(), [80], folder, source='simulation', current_zero=ref)
            job.run()
            self.assertEqual(job.snapshot()['status'], 'UNVERIFIED', job.snapshot())
            self.assertEqual(h.drives, [False, True, True])
            self.assertTrue(audit_session(job.recorder.path)['data_integrity_ok'])
            self.assertEqual(profile().trip_codes(), (4029755, 5641657, 75497))
            for result in job.results:
                self.assertAlmostEqual(result['current_dc_a'] - result['current_dc_corrected_a'], .0033, delta=.00002)
            summary_path = job.recorder.path / 'summary.json'
            original_summary = summary_path.read_text()
            data = json.loads(original_summary); data['results'][0]['current_dc_corrected_a'] += .01
            summary_path.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'current zero correction'):
                audit_session(job.recorder.path)
            summary_path.write_text(original_summary, encoding='utf-8')
            path = job.recorder.path / '000_result.json'
            data = json.loads(path.read_text()); data['offset_applied_a'] = 0
            path.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'preflight'):
                audit_session(job.recorder.path)

    def test_legacy_preflight_record_still_reconstructs(self):
        with tempfile.TemporaryDirectory() as folder:
            job = MeasurementJob(SyntheticHardware(), profile(), Settings(), [80], folder, source='simulation')
            job.run()
            path = job.recorder.path / '000_result.json'
            current = json.loads(path.read_text())
            legacy = {key: current[key] for key in ('kind', 'voltage_v', 'leakage_a', 'valid')}
            path.write_text(json.dumps(legacy), encoding='utf-8')
            self.assertTrue(audit_session(job.recorder.path)['data_integrity_ok'])

    def test_pending_profile_stays_locked_before_files_or_capture(self):
        with tempfile.TemporaryDirectory() as folder:
            h = OffsetHardware()
            p = replace(profile(), polarity_verified=False)
            with self.assertRaises(ValueError):
                MeasurementJob(h, p, Settings(), [80], Path(folder) / 'never', source='simulation',
                               current_zero=CurrentZeroReference(reference_document()))
            self.assertEqual(h.drives, [])
            self.assertFalse((Path(folder) / 'never').exists())


if __name__ == '__main__': unittest.main(verbosity=2)
