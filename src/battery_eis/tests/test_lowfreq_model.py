import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ps'))
from configuration import ABI,CLOCK_HZ,Settings,DEFAULT_SWEEP,RAW_CAPACITY,phase_increment
from hardware import BatteryHardware,MOMENTS
from synthetic import make_capture,SyntheticHardware,sine_lut
from analysis import analyze,complex_value,moments_from_raw
from calibration import Calibration,from_reference
from calibration_plan import reference_template
from session import MeasurementJob
from model_bridge import HealthModel
from panel import Painter,ai_label
from test_battery_eis import profile,reference

MODELS=ROOT/'models/frozen_two_frequency_20261022'


def dual_calibration(p):
    refs=[reference(Settings().for_frequency(f))['rows'][0] for f in DEFAULT_SWEEP]
    for row in refs:
        row['binding']=p.binding(Settings().for_frequency(row['binding']['phase_increment']*CLOCK_HZ/2**32))
    return Calibration(from_reference(dict(id='synthetic-only-dual',evidence_kind='independent_reference',
                        reference_source='unit test fixture, not physical',rows=refs)))


class LowFrequencyTests(unittest.TestCase):
    def test_window_allocation_actual_cycles_capacity_and_budget(self):
        plan=[Settings().for_frequency(f) for f in DEFAULT_SWEEP]
        for s in plan+[Settings().for_frequency(2)]:
            self.assertGreaterEqual(s.actual_frequency_hz*s.capture_s,8)
            self.assertLessEqual(s.capture_s,4)
            self.assertLess(len(make_capture(s,profile()).raw),RAW_CAPACITY)
        self.assertGreater(len(make_capture(plan[0],profile()).raw),4096)
        self.assertAlmostEqual(plan[1].capture_s,1)
        budget=.4+sum(2*(s.capture_s+s.settle_s) for s in plan)
        self.assertLess(budget,12)
        self.assertGreater(budget,11)

    def test_lowfreq_known_complex_sign_and_scale(self):
        for f in (2,2.16,17.8,100):
            s=Settings().for_frequency(f)
            r=analyze(make_capture(s,profile()),s,profile())
            expected=.01+.03/(1+1j*2*np.pi*s.actual_frequency_hz*.01)
            self.assertLess(abs(complex_value(r['impedance_ohm'])-expected)/abs(expected),.003)
            self.assertTrue(r['quality_ok'],r['reasons'])

    def test_long_window_binding_rejects_be02_and_short_window(self):
        p=profile();s=Settings().for_frequency(2.16)
        data=from_reference(reference(s));data['rows'][0]['binding']['abi']=0xbe02
        with self.assertRaisesRegex(ValueError,'exact binding'),tempfile.TemporaryDirectory() as folder:
            MeasurementJob(SyntheticHardware(),p,Settings(),[2.16],folder,calibration=Calibration(data))
        binding=p.binding(s)
        self.assertEqual(binding['raw_capacity_samples'],16384)
        self.assertEqual(binding['abi'],0xbe03)

    def test_low_frequency_calibration_has_actual_windows(self):
        rows=reference_template(profile(),frequencies=[1,2.16,17.8,1000])['rows']
        self.assertEqual([r['acquisition_supported'] for r in rows],[False,True,True,False])
        self.assertGreater(rows[1]['binding']['capture_ticks'],300000000)
        self.assertEqual(rows[2]['binding']['capture_ticks'],100000000)

    def test_driver_reads_addresses_above_4095_and_8191(self):
        h=BatteryHardware.__new__(BatteryHardware)
        h.control=MOMENTS;h.expected_sequence=7
        status=(ABI<<16)|(7<<8)|21
        h.read_status=lambda:status
        h.page=lambda page: (8200,12345,0,7) if page==9 else (0,0,0,0)
        class Config:
            address=0
            def write(self,offset,value):self.address=value & (RAW_CAPACITY-1)
        cfg=Config();h.config=cfg
        class Left:
            def read(self,offset):return cfg.address if offset==0 else cfg.address+100
        class Right:
            def read(self,offset):return 123 if offset==0 else cfg.address%2
        h.left=Left();h.right=Right()
        raw=h.read_capture().raw
        self.assertEqual(raw[4096,0],4096);self.assertEqual(raw[8192,0],8192)
        self.assertEqual(raw[-1,0],8199)
        with self.assertRaises(TimeoutError):h.read_capture(deadline=time.monotonic()-1)

    def test_long_rtl_capture_all_moments_match(self):
        folder=ROOT/'build/sim_lowfreq'
        if not folder.exists():folder=ROOT.parents[1]/'sim/evidence/rtl_lowfreq'
        self.assertTrue((folder/'run.log').exists(),'run simulate_lowfreq.ps1 first')
        log=(folder/'run.log').read_bytes()
        self.assertIn('PASS_LOWFREQ_BUFFER',log.decode('utf-16' if log.startswith(b'\xff\xfe') else 'utf-8-sig'))
        raw=np.loadtxt(folder/'lowfreq_raw.csv',delimiter=',',skiprows=1,dtype=np.int64)
        stats=np.loadtxt(folder/'lowfreq_stats.csv',delimiter=',',skiprows=1,dtype=np.int64)
        self.assertGreater(len(raw),12000)
        calculated=moments_from_raw(raw,sine_lut())
        np.testing.assert_array_equal(calculated[0],stats[:,1])
        np.testing.assert_array_equal(calculated[1],stats[:,2])


class ModelIntegrationTests(unittest.TestCase):
    def test_frozen_copy_hashes_and_independent_examples(self):
        for row in json.loads((MODELS/'source_manifest.json').read_text()):
            self.assertEqual(hashlib.sha256((ROOT/row['path']).read_bytes()).hexdigest(),row['sha256'])
        for name,expected in [('lib_two_ridge',.8386107271642403),('kit_two_forest',.9704620835440327)]:
            model=HealthModel(MODELS/(name+'.json'))
            sample=json.loads((MODELS/(name+'.example.json')).read_text())
            result=model.public_replay(sample)
            self.assertAlmostEqual(result['soh'],expected,12)
            self.assertEqual(result['status'],'research_estimate')
            self.assertFalse(result['valid_for_board']);self.assertIsNone(result['rul'])
            for change in ({'source_domain':'battery_eis_rev_a'},{'calibration_valid':False},
                           {'temperature_C':20},{'soc_percent':49},{'frequency_hz':[20,80]}):
                self.assertEqual(model.public_replay(dict(sample,**change))['status'],'rejected')

    def test_full_dual_workflow_calibrated_features_no_domain_spoof(self):
        p=profile();model=HealthModel(MODELS/'lib_two_ridge.json')
        with tempfile.TemporaryDirectory() as folder:
            job=MeasurementJob(SyntheticHardware(),p,Settings(),DEFAULT_SWEEP,folder,
                               calibration=dual_calibration(p),model=model,source='simulation',
                               metadata={'temperature_c':23,'soc_fraction':.5,'source_domain':'lib_public_dataset'})
            job.run();state=job.snapshot()
            self.assertEqual(state['status'],'DONE',state)
            summary=json.loads((Path(state['output'])/'summary.json').read_text())
            ai=summary['ai'];self.assertEqual(ai['reason'],'domain_requires_validation')
            self.assertIsNone(ai['soh']);self.assertIsNone(ai['rul'])
            self.assertEqual(ai['input']['soc_percent'],50)
            self.assertEqual(ai['input']['source_domain'],'simulation')
            for i,f in enumerate(ai['input']['frequency_hz']):
                zs=[complex(*r['impedance_ohm']) for r in job.results if r['frequency_hz']==f]
                self.assertAlmostEqual(ai['input']['z_real_ohm'][i],(sum(zs)/len(zs)).real)
                self.assertAlmostEqual(ai['input']['z_imag_ohm'][i],(sum(zs)/len(zs)).imag)
            self.assertLess(summary['timing']['window_budget_s'],12)
            # A sub-tick stage can legitimately measure 0 on Windows hosts whose
            # monotonic clock is GetTickCount64 (15.625ms); the field must exist
            # and be nonnegative. Result/phasor assertions above prove it ran.
            self.assertGreaterEqual(summary['timing']['stages_s']['analysis'],0)

    def test_uncalibrated_workflow_cannot_produce_soh(self):
        with tempfile.TemporaryDirectory() as folder:
            model=HealthModel(MODELS/'lib_two_ridge.json')
            job=MeasurementJob(SyntheticHardware(),profile(),Settings(),DEFAULT_SWEEP,folder,
                               model=model,source='simulation')
            job.run()
            self.assertEqual(job.snapshot()['ai']['reason'],'measurement_or_calibration_invalid')
            self.assertIsNone(job.snapshot()['ai']['soh'])

    def test_whole_session_deadline_stops_before_load(self):
        class Slow(SyntheticHardware):
            def initialize(self):
                job.deadline=time.monotonic()-1
                return super().initialize()
        with tempfile.TemporaryDirectory() as folder:
            h=Slow();job=MeasurementJob(h,profile(),Settings(),DEFAULT_SWEEP,folder,source='simulation')
            job.run()
            self.assertEqual(job.snapshot()['status'],'TIMEOUT')
            self.assertEqual(h.sequence,0);self.assertFalse(h.active)

    def test_over_budget_and_duplicate_plan_rejected_before_capture(self):
        with tempfile.TemporaryDirectory() as folder:
            for frequencies,budget in ((DEFAULT_SWEEP,10),([2.16,2.16],20)):
                h=SyntheticHardware()
                with self.assertRaises(ValueError):
                    MeasurementJob(h,profile(),Settings(),frequencies,folder,budget_s=budget)
                self.assertEqual(h.sequence,0)

    def test_panel_research_and_pending_without_missing_glyphs(self):
        painter=Painter()
        for ai in [dict(status='research_estimate',soh=.8386),dict(status='rejected',reason='domain_requires_validation'),
                   dict(status='rejected',reason='measurement_or_calibration_invalid')]:
            painter.render(dict(status='UNVERIFIED',source='public_dataset_replay',ai=ai))
            self.assertNotIn('?', ''.join(row[-1] for row in painter.bounds))
        self.assertEqual(ai_label({'status':'rejected','soh':.9}),'数据待核验')


if __name__=='__main__':unittest.main(verbosity=2)
