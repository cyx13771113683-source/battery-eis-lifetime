from copy import deepcopy
from dataclasses import asdict,replace
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ps'))
from prototype import PrototypeCalibration,PrototypeModel,PrototypeJob,FastHardware,safety_profile,check_zero
from calibration import Calibration
from configuration import Settings
from hardware import BatteryHardware
from synthetic import make_capture,moments_from_raw,sine_lut,SyntheticHardware
from ui_records import health_values,RecordIndex
from test_battery_eis import profile


def pending_profile():
    return replace(profile(),adc_clock_verified=False,polarity_verified=False,limits_confirmed=False)


def calibration():
    p=pending_profile()
    return PrototypeCalibration(dict(schema='battery-prototype-calibration/1',id='test-synthetic',mode='prototype',
        enabled=True,valid_for_board=False,rows=[dict(binding=p.binding(Settings().for_frequency(f)),correction=[.72,-.003])
                                               for f in (80.,17.8,2.16)]))


def model():return PrototypeModel(ROOT/'models/frozen_two_frequency_20261022/lib_two_ridge.json')


class TestHardware(SyntheticHardware):
    def command(self,op,value):pass


class ReplayJob(PrototypeJob):
    def capture(self,settings,drive):
        self.check();self.hardware.sequence+=1
        c=make_capture(settings,self.profile,self.hardware.sequence,drive=drive)
        c.raw[c.raw[:,3]==0,2]-=round(.26/self.profile.voltage_divider/self.profile.code_volts)
        c.moments=moments_from_raw(c.raw,self.lut)
        return c


class PrototypeTests(unittest.TestCase):
    def test_production_profile_and_api_stay_locked(self):
        p=pending_profile();before=asdict(p);q=safety_profile(p)
        self.assertEqual(asdict(p),before)
        with self.assertRaises(ValueError):q.validate(arm=True)
        with self.assertRaises(ValueError):Calibration(calibration().document)

    def test_ratio_application_keeps_raw_channels_and_provenance(self):
        p=pending_profile();s=Settings().for_frequency(80)
        from analysis import analyze
        raw=analyze(make_capture(s,p),s,p)
        corrected=calibration().apply(raw,p.binding(s))
        self.assertEqual(corrected['raw_impedance_ohm'],raw['impedance_ohm'])
        self.assertEqual(corrected['current_ac_a'],raw['current_ac_a'])
        self.assertEqual(corrected['voltage_ac_v'],raw['voltage_ac_v'])
        self.assertFalse(corrected['valid']);self.assertIsNone(corrected['calibration_id'])

    def test_calibration_binding_and_state_are_checked(self):
        c=calibration();d=c.document;d['valid_for_board']=True
        with self.assertRaises(ValueError):PrototypeCalibration(d)
        with self.assertRaises(ValueError):c.ratio({'wrong':'binding'})

    def test_zero_envelope_rejects_noise_and_bad_counts(self):
        row=dict(reasons=['absolute_zero_current_over_1mA'],counts=[305,305],voltage_v=3.44,
                 raw_abs_max_a=.004,noise_std_a=.00013,residual_peak_a=.0005,half_window_drift_a=0.)
        check_zero(row)
        for name,value in [('voltage_v',4.2),('noise_std_a',.001),('counts',[0,0])]:
            with self.subTest(name=name),self.assertRaises(ValueError):check_zero(dict(row,**{name:value}))

    def test_completed_prototype_session_is_separate_from_production(self):
        with tempfile.TemporaryDirectory() as directory:
            job=ReplayJob(TestHardware(),pending_profile(),Settings(),[80,17.8,2.16],directory,
                calibration(),source='simulation',model=model())
            job.run();self.assertEqual(job.snapshot()['status'],'DONE',job.snapshot())
            summary=json.loads((job.recorder.path/'summary.json').read_text())
            self.assertEqual(summary['schema'],'battery-eis-prototype/1')
            self.assertFalse(any(r['valid'] for r in summary['results']))
            self.assertIsNone(summary['ai']['rul'])
            self.assertEqual(RecordIndex(directory).page(0)[0]['source'],'simulation')

    def test_cancel_never_returns_soh(self):
        with tempfile.TemporaryDirectory() as directory:
            job=ReplayJob(TestHardware(),pending_profile(),Settings(),[80],directory,
                calibration(),source='simulation',model=model())
            job.cancel_event.set();job.run()
            self.assertEqual(job.snapshot()['status'],'CANCELLED')
            self.assertIsNone(job.snapshot()['ai']['soh'])

    def test_model_rejects_incomplete_spectrum(self):
        self.assertIsNone(model().evaluate([])['soh'])

    def test_ui_uses_result_label_without_invented_years(self):
        health=health_values(dict(status='prototype_result',soh=.763,capacity_Ah=None,rul=None,valid_for_board=False))
        self.assertEqual(health['soh'],'SOH健康度 76.3%')
        self.assertNotIn('估计',health['soh']);self.assertNotIn('研究',health['soh'])
        self.assertEqual(health['rul'],'剩余寿命 --')

    def test_fast_and_standard_readback_match_exactly(self):
        c=make_capture(Settings(frequency_hz=80,capture_s=.2,settle_s=.2),profile())
        rows=c.raw
        index=[0]
        class Config:
            def __setitem__(self,key,value):index[0]=int(value)&16383
        class Data:
            def __init__(self,side):self.side=side
            def __getitem__(self,key):
                column=(0 if key==0 else 1)+(0 if self.side==0 else 2)
                return int(rows[index[0],column])&0xffffffff
        cfg=Config();left=Data(0);right=Data(1)
        h=FastHardware.__new__(FastHardware);h.control=0;h.expected_sequence=1
        h.config=SimpleNamespace(array=cfg,write=lambda offset,value:cfg.__setitem__(offset//4,value))
        h.left=SimpleNamespace(array=left,read=lambda offset:left[offset//4])
        h.right=SimpleNamespace(array=right,read=lambda offset:right[offset//4])
        h.read_status=lambda:c.meta['status']
        def page(j):
            if j==9:return len(rows),c.meta['configuration'][2],0,1
            if j==10:return c.meta['adc_registers']
            if j==11:return c.meta['configuration']
            a=int(c.moments[0][j])&((1<<64)-1);b=int(c.moments[1][j])&((1<<64)-1)
            return a&0xffffffff,a>>32,b&0xffffffff,b>>32
        h.page=page
        fast=h.read_capture();slow=BatteryHardware.read_capture(h)
        np.testing.assert_array_equal(fast.raw,slow.raw)
        np.testing.assert_array_equal(fast.raw,c.raw)
        self.assertEqual(fast.moments,slow.moments)
        with self.assertRaises(TimeoutError):h.read_capture(deadline=time.monotonic()-1)
