"""Frequency-specific reference templates and coverage; never arms hardware."""
from dataclasses import replace
import cmath
import math

from configuration import ABI, CLOCK_HZ, Settings, finite, RAW_CAPACITY, MIN_FREQUENCY, MAX_FREQUENCY, MAX_CAPTURE_S
from analysis import complex_value

# 1/2/5 per decade, retained 40/80 Hz points and the two nominal RC knees.
CALIBRATION_FREQUENCIES = (1., 2., 5., 10., 20., 40., 50., 80., 100.,
                           200., 500., 1000., 2000., 3200., 5000., 8000., 10000.)


def frequency_rows(profile, settings, frequencies):
    profile.validate(arm=False)
    settings.validate()
    frequencies = tuple(frequencies)
    if not 1 <= len(frequencies) <= 20:
        raise ValueError('choose 1..20 calibration frequencies')
    seen = set()
    rows = []
    for value in frequencies:
        frequency = finite(value, 1., 10000., 'calibration frequency_hz')
        if frequency in seen:
            raise ValueError('duplicate calibration frequency')
        seen.add(frequency)
        row = dict(requested_frequency_hz=frequency,
                   minimum_capture_s_at_requested_frequency=8 / frequency,
                   acquisition_supported=False, actual_frequency_hz=None,
                   binding=None, blocked_reason=None)
        try:
            selected = settings.for_frequency(frequency)
        except ValueError as exc:
            row['blocked_reason'] = str(exc)
        else:
            row.update(acquisition_supported=True,
                       actual_frequency_hz=selected.actual_frequency_hz,
                       binding=profile.binding(selected))
        rows.append(row)
    return rows


def reference_template(profile, settings=Settings(), frequencies=CALIBRATION_FREQUENCIES):
    rows = frequency_rows(profile, settings, frequencies)
    for row in rows:
        row.update(measured_voltage_ac_v=None, reference_voltage_ac_v=None,
                   measured_current_ac_a=None, reference_current_ac_a=None,
                   voltage_range_v=None, current_ac_range_a=None,
                   reference_uncertainty_relative=None)
    return dict(schema='battery-transfer-reference-template/1', id=None,
                evidence_kind='pending_reference', reference_source=None,
                board_validated=False,
                instructions='Fill independent common-phase reference measurements; copy each real capture binding. '
                             'Unsupported rows require a separately validated acquisition implementation. '
                             'Do not replace missing coefficients with nominal RC calculations.',
                acquisition_limits=dict(abi=ABI, frequency_range_hz=[MIN_FREQUENCY, MAX_FREQUENCY],
                    maximum_capture_s=MAX_CAPTURE_S, minimum_cycles=8, raw_capacity_samples=RAW_CAPACITY,
                    nominal_samples_per_channel_s=CLOCK_HZ / (32768 * 2),
                    note='BE03 candidate multiplexed acquisition; DAC arbitration can reduce throughput. '
                         'This template does not extend firmware bandwidth or validate hardware.'),
                rows=rows)


def calibration_coverage(calibration, profile, settings, frequencies):
    rows = frequency_rows(profile, settings, frequencies)
    for row in rows:
        if not row['acquisition_supported']:
            row['status'] = 'unsupported_acquisition'
            continue
        matches = [entry for entry in calibration.document['rows'] if entry['binding'] == row['binding']]
        row['status'] = 'matched' if len(matches) == 1 else 'missing_calibration'
        if len(matches) == 1:
            for channel in ('voltage', 'current'):
                correction = complex_value(matches[0][channel + '_correction'])
                row[channel + '_gain'] = abs(correction)
                row[channel + '_phase_deg'] = math.degrees(cmath.phase(correction))
    return dict(schema='battery-calibration-coverage/1', calibration_id=calibration.document['id'],
                complete=all(row['status'] == 'matched' for row in rows),
                scope='Exact binding coverage only; measured voltage/current envelopes and quality are checked at capture.',
                rows=rows)
