"""Signed zero-drive diagnostics; a live mean cannot certify its own zero.

An independent, bounded zero reference may authorize session-local DC reporting
correction. Raw ADC/PL protection and AC phasors are never changed here.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
from configuration import ABI, Settings, digest, finite, phase_increment


def preflight_settings(settings=None):
    return replace(settings or Settings(), frequency_hz=80., settle_s=.2, capture_s=.2)


def _utc(value):
    try:
        result = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('zero reference needs ISO UTC timestamps') from exc
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise ValueError('zero reference timestamps must include UTC timezone')
    return result


class CurrentZeroReference:
    def __init__(self, document):
        d = deepcopy(document)
        if d.get('schema') != 'battery-current-zero/1':
            raise ValueError('unknown current zero reference schema')
        if d.get('known_zero_verified') is not True:
            raise ValueError('independent known zero must be verified; live mean is not evidence')
        for key in ('id', 'known_zero_evidence'):
            if not isinstance(d.get(key), str) or not d[key].strip():
                raise ValueError('zero reference needs ' + key)
        sha = d.get('evidence_sha256', '')
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
            raise ValueError('zero reference needs independent evidence SHA256')
        if d.get('source') not in ('simulation', 'hardware'):
            raise ValueError('zero reference source must be hardware or simulation')
        finite(d.get('offset_a'), -.005, .005, 'zero reference offset_a')
        finite(d.get('uncertainty_a'), 0., .0001, 'zero reference uncertainty_a')
        envelope = d.get('voltage_range_v')
        if not isinstance(envelope, list) or len(envelope) != 2:
            raise ValueError('zero reference needs narrow voltage_range_v')
        low = finite(envelope[0], .1, 5., 'zero reference minimum voltage')
        high = finite(envelope[1], low, min(5., low + .201), 'zero reference maximum voltage')
        if high <= low:
            raise ValueError('zero reference voltage interval must have nonzero width')
        if not isinstance(d.get('binding'), dict):
            raise ValueError('zero reference binding required')
        if type(d.get('adc_config')) is not int or d['adc_config'] != 0xf0000130 or d.get('slot_cycles') != 32768:
            raise ValueError('zero reference must use BE03 buffer-off ADC configuration')
        created, expires = _utc(d.get('created_utc')), _utc(d.get('expires_utc'))
        if not 0 < (expires - created).total_seconds() <= 86400:
            raise ValueError('zero reference validity must be at most 24 hours')
        digest(d)  # Reject non-finite extension values too.
        self._document = d

    @property
    def document(self):
        return deepcopy(self._document)

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text(encoding='utf-8-sig')))

    def validate_for(self, profile, source, at_utc=None, settings=None):
        d = self._document
        if d['source'] != source:
            raise ValueError('zero reference source differs from session source')
        if d['binding'] != profile.binding(preflight_settings(settings)):
            raise ValueError('zero reference hardware/ADC binding differs')
        at_utc = at_utc or datetime.now(timezone.utc)
        if not _utc(d['created_utc']) <= at_utc <= _utc(d['expires_utc']):
            raise ValueError('zero reference expired or not yet valid')
        return self


def evaluate_zero_drive(capture, profile, reference=None, source='hardware', at_utc=None, settings=None):
    """Pure, replayable evaluation. Returns diagnostics even on a failed check.

    Peak residual is max(abs(i[n] - mean(i))), not abs(max(abs(i))-mean(i)).
    Noise is measured from the samples; a missing statistic gets no default.
    The reference only permits correction inside its measured voltage envelope.
    """
    reasons = []
    result = dict(kind='zero_drive_preflight', policy='absolute-1mA-v1' if reference is None else 'referenced-tare-v1',
        valid=False, reasons=reasons, offset_applied_a=0., physical_leakage_excluded=False,
        scope='apparent ADC current; correction is not proof of zero physical current', counts=[0, 0])
    raw = np.asarray(capture.raw)
    if raw.ndim != 2 or raw.shape[1] != 4 or not len(raw) or not np.issubdtype(raw.dtype, np.integer):
        reasons.append('malformed_raw_capture'); return result
    status = capture.meta.get('status', 0)
    if status >> 16 != ABI or status & 0x56 != 0x14 or capture.meta.get('faults'):
        reasons.append('preflight_status_or_PL_fault')
    adc = capture.meta.get('adc_registers', [])
    if len(adc) != 4 or adc[0] & 0xfffffffe != 0xf0000130 or adc[3] != 32768:
        reasons.append('preflight_ADC_configuration_mismatch')
    check = preflight_settings(settings)
    expected = [phase_increment(80.), 20000000, 20000000, check.gate_high_code]
    if list(capture.meta.get('configuration', [])) != expected:
        reasons.append('preflight_capture_configuration_mismatch')
    if np.any((raw[:, 3] != 0) & (raw[:, 3] != 1)) or np.any(np.diff(raw[:, 0]) <= 0) or np.any(np.diff(raw[:, 3]) == 0):
        reasons.append('preflight_sample_order_invalid')
    if np.any(raw[:, 1] != (raw[:, 0] * expected[0]) % (2**32)):
        reasons.append('preflight_phase_timestamp_mismatch')
    if np.any(raw[:, 2] <= -8388608) or np.any(raw[:, 2] >= 8388607):
        reasons.append('preflight_ADC_saturation')
    counts = [int(np.sum(raw[:, 3] == ch)) for ch in (0, 1)]
    result['counts'] = counts
    if min(counts) < 100:
        reasons.append('preflight_insufficient_samples'); return result
    v = raw[raw[:, 3] == 0, 2] * profile.code_volts * profile.voltage_divider * profile.voltage_dc_scale
    i = raw[raw[:, 3] == 1, 2] * profile.code_volts / profile.r_shunt_ohm * profile.current_dc_scale
    mean, peak = float(i.mean()), float(np.abs(i).max())
    residual = i - mean
    result.update(voltage_v=float(v.mean()), raw_mean_a=mean, raw_abs_max_a=peak,
        raw_min_a=float(i.min()), raw_max_a=float(i.max()), noise_std_a=float(i.std(ddof=1)),
        residual_peak_a=float(np.abs(residual).max()),
        half_window_drift_a=float(i[len(i)//2:].mean() - i[:len(i)//2].mean()))
    if not profile.minimum_voltage_v <= result['voltage_v'] <= profile.maximum_voltage_v:
        reasons.append('preflight_voltage_outside_profile')
    # Independent absolute guard: no offset can suppress these raw limits.
    if peak > min(.010, profile.maximum_current_a):
        reasons.append('raw_zero_current_over_10mA')
    if reference is None:
        result['leakage_a'] = mean  # Legacy field, uncorrected; apparent current only.
        if peak > .001:
            reasons.append('absolute_zero_current_over_1mA')
    else:
        try:
            reference.validate_for(profile, source, at_utc, settings)
        except ValueError as exc:
            reasons.append(str(exc))
        d = reference.document
        result.update(reference_id=d['id'], reference_sha256=digest(d), reference_offset_a=d['offset_a'],
                      mean_minus_reference_a=mean-d['offset_a'])
        if not d['voltage_range_v'][0] <= result['voltage_v'] <= d['voltage_range_v'][1]:
            reasons.append('zero_reference_voltage_envelope_mismatch')
        if abs(mean-d['offset_a']) + d['uncertainty_a'] > .0005:
            reasons.append('zero_mean_differs_from_independent_reference')
        if result['noise_std_a'] > .0005:
            reasons.append('zero_noise_std_over_0.5mA')
        if result['residual_peak_a'] > .0005:
            reasons.append('zero_residual_peak_over_0.5mA')
        if abs(result['half_window_drift_a']) > .0002:
            reasons.append('zero_drift_over_0.2mA')
        if not reasons:
            result['offset_applied_a'] = mean
    result['valid'] = not reasons
    return result
