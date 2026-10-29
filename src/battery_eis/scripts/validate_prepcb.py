"""Reproducible synthetic two-frequency workflow and frozen-public-data replay.

No PYNQ import, overlay load, physical calibration or battery health validation.
"""
from dataclasses import replace,asdict
import hashlib
import json
from pathlib import Path
import sys
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ps'))
from configuration import HardwareProfile,Settings,DEFAULT_SWEEP
from calibration_plan import reference_template
from calibration import Calibration,from_reference
from synthetic import SyntheticHardware
from session import MeasurementJob,json_write
from model_bridge import HealthModel

def main():
    output=Path(sys.argv[1])
    output.mkdir(parents=True,exist_ok=False)
    profile=replace(HardwareProfile.load(ROOT/'config/profile.example.json'),
                    hardware_id='SIMULATION',board_serial='SIMULATION',module_serial='SIMULATION',
                    wiring_verified=True,module_verified=True,adc_clock_verified=True,
                    polarity_verified=True,limits_confirmed=True)
    reference=reference_template(profile,frequencies=DEFAULT_SWEEP)
    reference.update(id='SYNTHETIC-ONLY-BE03',evidence_kind='independent_reference',
                     reference_source='Synthetic identity transfer for software tests only',simulation_only=True)
    for row in reference['rows']:
        row.update(measured_voltage_ac_v=[.001,0],reference_voltage_ac_v=[.001,0],
                   measured_current_ac_a=[.01,0],reference_current_ac_a=[.01,0],
                   voltage_range_v=[3,4.2],current_ac_range_a=[.001,.4],reference_uncertainty_relative=.005)
    calibration=Calibration(from_reference(reference))
    json_write(output/'synthetic_profile.json',asdict(profile))
    json_write(output/'synthetic_reference.json',reference)
    json_write(output/'synthetic_transfer.json',calibration.document)
    models=ROOT/'models/frozen_two_frequency_20261022'
    model=HealthModel(models/'lib_two_ridge.json')
    started=time.monotonic()
    job=MeasurementJob(SyntheticHardware(),profile,Settings(),DEFAULT_SWEEP,output,
                       calibration=calibration,model=model,source='simulation',
                       metadata={'temperature_c':23,'soc_fraction':.5})
    job.run();state=job.snapshot()
    assert state['status']=='DONE',state
    assert state['ai']['reason']=='domain_requires_validation' and state['ai']['soh'] is None
    errors=[]
    for result in job.results:
        expected=.01+.03/(1+1j*2*np.pi*result['frequency_hz']*.01)
        error=abs(complex(*result['impedance_ohm'])-expected)/abs(expected)
        assert error<.003
        errors.append(error)
    public={}
    for name,expected in [('lib_two_ridge',.8386107271642403),('kit_two_forest',.9704620835440327)]:
        sample=json.loads((models/(name+'.example.json')).read_text())
        ai=HealthModel(models/(name+'.json')).public_replay(sample)
        assert abs(ai['soh']-expected)<1e-12 and not ai['valid_for_board'] and ai['rul'] is None
        json_write(output/(name+'_public_replay.json'),ai)
        public[name]=dict(soh=ai['soh'],valid_for_board=False,rul=None)
    summary=dict(source='synthetic_and_public_replay',model_input=state['ai']['input'],
                 simulation_session=state['output'],simulation_relative_errors=errors,
                 host_elapsed_s=time.monotonic()-started,window_budget_s=job.window_budget_s,
                 default_plan=[asdict(s) for s in job.plan],public_examples=public,
                 board_io_exercised=False,physical_calibration=False,
                 target_battery_accuracy_validated=False,full_20s_hardware_validated=False,
                 timing_note='Synthetic capture returns quickly; elapsed time is not physical acquisition latency')
    json_write(output/'validation.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
