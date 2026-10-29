"""Read-only comparison of PL moments and the timestamp/trend diagnostic fit.

The alternative is a research estimate, never a replacement calibrated result.
"""
import argparse
import math
from pathlib import Path
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ps'))
from analysis import diagnose,complex_value,pair
from calibration import Calibration
from configuration import HardwareProfile
from session import json_write
from session_audit import audit_session,load_json,_read_raw


def diagnose_session(directory,calibration_path=None):
    path=Path(directory).resolve()
    audit=audit_session(path,calibration_path)
    session=load_json(path/'session.json');summary=load_json(path/'summary.json')
    profile=HardwareProfile(**session['profile']).validate()
    calibration=None
    if (path/'calibration_used.json').is_file():
        calibration=Calibration(load_json(path/'calibration_used.json')['document'])
    if calibration_path is not None:calibration=Calibration.load(calibration_path)
    windows=[]
    for saved in summary['results']:
        raw=_read_raw(path/saved['raw_file'])
        fits=[diagnose(raw,ch,saved['frequency_hz']) for ch in (0,1)]
        corrections=(1+0j,1+0j)
        if calibration:
            # Reuse solely for a same-record comparison. A different estimator
            # still requires its own measured transfer calibration and binding.
            row=next(r for r in calibration.document['rows'] if r['binding']==saved['binding'])
            uncorrected_i=abs(complex_value(saved['current_ac_a']))/abs(complex_value(row['current_correction']))
            corrections,_=calibration.corrections(saved['binding'],saved['voltage_dc_v'],uncorrected_i)
        v=fits[0]['phasor']*profile.code_volts*profile.voltage_divider*corrections[0]
        i=fits[1]['phasor']*profile.code_volts/profile.r_shunt_ohm*corrections[1]
        alternative=-v/i if abs(i)>1e-12 else None
        baseline=complex_value(saved['impedance_ohm'])
        delta=alternative-baseline if alternative is not None else None
        windows.append(dict(raw_file=saved['raw_file'],frequency_hz=saved['frequency_hz'],repeat=saved['repeat'],
            production_impedance_ohm=pair(baseline),production_quality_ok=saved['quality_ok'],
            diagnostic_impedance_ohm=pair(alternative) if alternative is not None else None,
            change_ohm=pair(delta) if delta is not None else None,
            relative_change_percent=100*abs(delta)/max(abs(baseline),1e-30) if delta is not None else None,
            phase_change_deg=math.degrees(np.angle(alternative/baseline)) if alternative is not None and abs(baseline)>1e-30 else None,
            voltage_linear_change_v=fits[0]['dc_drift_codes']*profile.code_volts*profile.voltage_divider,
            current_linear_change_a=fits[1]['dc_drift_codes']*profile.code_volts/profile.r_shunt_ohm,
            diagnostic_valid_for_measurement=False))
    return dict(schema='battery-session-diagnostic/1',source=session['source'],audit=audit,windows=windows,
        production_results_modified=False,diagnostic_valid_for_measurement=False,model_evaluation_allowed=False,
        calibration_status='Original PL calibration reused only to compare; alternative estimator is not calibrated',
        interpretation='Estimator disagreement is a diagnostic, not an absolute error or a correction factor',
        limitations=['Trend fitting cannot establish stationarity or remove same-frequency interference',
            'No threshold here certifies accuracy; compare against independent physical reference',
            'Original quality/repeatability decisions remain in the saved session'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session',type=Path,required=True)
    parser.add_argument('--calibration',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.resolve().is_relative_to(args.session.resolve()):
        raise ValueError('diagnostic output must be outside the original session')
    json_write(args.output,diagnose_session(args.session,args.calibration))


if __name__=='__main__':main()
