"""Finite PS-only timing study, with optional wall-clock synthetic capture waits.

Never loads a bitstream or accesses an ADC, DAC, MMIO, or display device.
Real-time mode reproduces the scheduled waiting time, not analog behaviour.
"""
import os
os.environ.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
import argparse
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ps'))
from configuration import HardwareProfile, Settings, DEFAULT_SWEEP
from calibration_plan import reference_template
from calibration import Calibration, from_reference
from synthetic import SyntheticHardware
from session import MeasurementJob, json_write
from model_bridge import HealthModel
from panel import Painter


class ScheduledSyntheticHardware(SyntheticHardware):
    def start(self, settings, profile, drive=True):
        started = time.monotonic()
        super().start(settings, profile, drive)
        self.ends = started + settings.settle_s + settings.capture_s


def fixture():
    profile = replace(HardwareProfile.load(ROOT / 'config/profile.example.json'),
        hardware_id='SIMULATION', board_serial='SIMULATION', module_serial='SIMULATION',
        wiring_verified=True, module_verified=True, adc_clock_verified=True,
        polarity_verified=True, limits_confirmed=True)
    reference = reference_template(profile, frequencies=DEFAULT_SWEEP)
    reference.update(id='SYNTHETIC-TIMING-ONLY', evidence_kind='independent_reference',
        reference_source='Synthetic identity transfer; not measured calibration', simulation_only=True)
    for row in reference['rows']:
        row.update(measured_voltage_ac_v=[.001, 0], reference_voltage_ac_v=[.001, 0],
            measured_current_ac_a=[.01, 0], reference_current_ac_a=[.01, 0],
            voltage_range_v=[3, 4.2], current_ac_range_a=[.001, .4],
            reference_uncertainty_relative=.005)
    return profile, reference, Calibration(from_reference(reference))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runs', type=int, default=3, choices=range(1, 11))
    parser.add_argument('--scheduled-waits', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    profile, reference, calibration = fixture()
    model_path = ROOT / 'models/frozen_two_frequency_20261022/lib_two_ridge.json'
    model = HealthModel(model_path)
    painter = Painter()
    json_write(args.output / 'synthetic_profile.json', asdict(profile))
    json_write(args.output / 'synthetic_reference.json', reference)
    rows = []
    for run in range(args.runs):
        hardware = ScheduledSyntheticHardware() if args.scheduled_waits else SyntheticHardware()
        began = time.monotonic()
        job = MeasurementJob(hardware, profile, Settings(), DEFAULT_SWEEP,
            args.output / ('run_%02d' % run), calibration=calibration, model=model,
            source='simulation', metadata={'temperature_c': 23, 'soc_fraction': .5})
        job.run()
        measured = time.monotonic()
        state = job.snapshot()
        frame = painter.render(state)
        rendered = time.monotonic()
        if state['status'] != 'DONE' or state['ai']['soh'] is not None:
            raise RuntimeError('timed workflow failed or exposed an invalid SOH: ' + repr(state))
        row = dict(run=run, session=state['output'], status=state['status'],
            model_reason=state['ai']['reason'], window_budget_s=job.window_budget_s,
            start_to_summary_s=measured-began, render_only_s=rendered-measured,
            start_to_frame_s=rendered-began, stage_timing=job.timing,
            frame_sha256=hashlib.sha256(frame.tobytes()).hexdigest(),
            records=sum(len(__import__('numpy').loadtxt(
                Path(state['output']) / (stem+'_raw.csv'), delimiter=',', skiprows=1))
                for stem in job.recorder.index))
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    result = dict(schema='battery-ps-timing/1', source='scheduled_synthetic' if args.scheduled_waits else 'synthetic',
        machine=platform.machine(), python=sys.version, scheduled_waits=args.scheduled_waits,
        runs=rows, maximum_start_to_frame_s=max(r['start_to_frame_s'] for r in rows),
        all_under_20s=all(r['start_to_frame_s'] < 20 for r in rows),
        hardware_io_exercised=False, physical_calibration=False,
        full_20s_hardware_validated=False, model_runs_on='PS',
        excluded=['real ADC self-calibration', 'real AXI frozen RAM readback',
                  'HDMI/DMA transfer', 'actual battery dynamics'],
        timing_note='Frame render is in-memory. Scheduled waits do not validate converter or battery timing.')
    json_write(args.output / 'benchmark.json', result)
    print(json.dumps({'status': 'PASS_SOFTWARE_ONLY', 'maximum_s': result['maximum_start_to_frame_s']}))


if __name__ == '__main__':
    main()
