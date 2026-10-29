import copy
from dataclasses import replace,asdict
import json
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ps'))
from configuration import HardwareProfile,Settings,phase_increment,ABI,ASSEMBLY_SHA256
from hardware import BatteryHardware
from analysis import analyze,moments_from_raw,complex_value,pair,repeatability
from synthetic import make_capture,sine_lut,SyntheticHardware
from calibration import Calibration,from_reference,ab_check,export_ab_session
from session import MeasurementJob
from panel import Painter,action,BUSY,NAV,NAV_RECTS

def profile():
    p=HardwareProfile.load(ROOT/'config/profile.example.json')
    return replace(p,hardware_id='test',board_serial='testboard',module_serial='testmodule',
                   cell_id='testcell',wiring_verified=True,module_verified=True,adc_clock_verified=True,
                   polarity_verified=True,limits_confirmed=True)

def reference(settings=None):
    s=settings or Settings();p=profile()
    return dict(id='test-reference',evidence_kind='independent_reference',reference_source='test fixture only',rows=[
        dict(binding=p.binding(s),measured_voltage_ac_v=[.001,0],reference_voltage_ac_v=[.00101,.00002],
             measured_current_ac_a=[.01,0],reference_current_ac_a=[.0099,-.0001],
             voltage_range_v=[3,4.2],current_ac_range_a=[.001,.4],reference_uncertainty_relative=.005)])

class ConfigurationTests(unittest.TestCase):
    def test_nominal_profile_cannot_arm(self):
        with self.assertRaises(ValueError):HardwareProfile.load(ROOT/'config/profile.example.json').validate(arm=True)
    def test_frequency_range_and_finiteness(self):
        for value in (0,1.99,100.1,float('nan'),float('inf'),True):
            with self.assertRaises(ValueError):phase_increment(value)
        self.assertEqual(phase_increment(80),3436)
    def test_clock_and_battery_limits(self):
        with self.assertRaises(ValueError):replace(profile(),adc_clock_hz=8000000).validate()
        with self.assertRaises(ValueError):replace(profile(),maximum_voltage_v=5.1).validate()
        with self.assertRaises(ValueError):Settings(frequency_hz=10,capture_s=.2).validate()
    def test_wrong_abi_rejected_without_write(self):
        class MMIO:
            def read(self,_):return 0x574a0001
            def write(self,*args):raise AssertionError('must not write legacy ABI')
        class IP:mmio=MMIO()
        class Overlay:waveshare_config=waveshare_status=left_result=right_result=IP()
        with self.assertRaisesRegex(RuntimeError,'BE03'):BatteryHardware(Overlay())
        MMIO.read=lambda self,_:0xbe010011
        with self.assertRaisesRegex(RuntimeError,'BE03'):BatteryHardware(Overlay())
    def test_final_assembly_units_and_protection_codes(self):
        p=profile()
        self.assertEqual(p.r_shunt_ohm,.1);self.assertEqual(p.voltage_divider,1.249)
        self.assertEqual(p.assembly_sha256,ASSEMBLY_SHA256)
        self.assertAlmostEqual(1/p.voltage_divider,10000/12490)
        self.assertEqual(p.trip_codes(),(4029755,5641657,75497))
        self.assertEqual(replace(p,maximum_voltage_v=5,maximum_current_a=.55).validate().trip_codes(),(4029755,6716259,92275))
        self.assertEqual(replace(p,r_shunt_ohm=.102,current_dc_scale=.8,maximum_current_a=.55).validate().trip_codes()[2],117650)
        self.assertAlmostEqual(4970031*p.code_volts*p.voltage_divider,3.7,places=6)
        self.assertAlmostEqual(75497*p.code_volts/p.r_shunt_ohm,.45,places=5)
    def test_old_assembly_profiles_and_unrepresentable_limits_refused(self):
        for changes in ({'r_shunt_ohm':10.},{'r_shunt_ohm':1.},{'voltage_divider':2.},
                        {'assembly_sha256':'0'*64},{'maximum_voltage_v':5.,'voltage_divider':1.24,'voltage_dc_scale':.8},
                        {'maximum_current_a':.56}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):replace(profile(),**changes).validate()

class AnalysisTests(unittest.TestCase):
    def setUp(self):self.p=profile();self.s=Settings();self.capture=make_capture(self.s,self.p)
    def test_known_complex_impedance_sign_and_scale(self):
        r=analyze(self.capture,self.s,self.p)
        f=self.s.actual_frequency_hz;expected=.01+.03/(1+1j*2*np.pi*f*.01)
        self.assertLess(abs(complex_value(r['impedance_ohm'])-expected)/abs(expected),.002)
        self.assertLess(r['phase_deg'],0)
        self.assertTrue(r['quality_ok']);self.assertFalse(r['valid'])
        self.assertEqual(r['reasons'],['uncalibrated'])
        self.assertIsNone(r['ai']['soh']);self.assertIsNone(r['ai']['rul'])
    def test_independent_corrections_apply_to_both_channels(self):
        cal=Calibration(from_reference(reference()))
        base=analyze(self.capture,self.s,self.p);corrected=analyze(self.capture,self.s,self.p,cal)
        cv,ci=cal.corrections(self.p.binding(self.s),3.7,.02)[0]
        expected=complex_value(base['impedance_ohm'])*cv/ci
        self.assertLess(abs(complex_value(corrected['impedance_ohm'])-expected),1e-12)
        self.assertTrue(corrected['valid'])
    def test_shifted_channel_sample_times_do_not_assume_simultaneity(self):
        r=analyze(self.capture,self.s,self.p)
        self.assertAlmostEqual(r['diagnostics'][0]['sample_rate_hz'],1525.87890625,5)
        self.assertNotEqual(self.capture.raw[0,0],self.capture.raw[1,0])
    def test_missing_duplicate_saturation_and_count_rejected(self):
        for mutate in ('missing','duplicate','saturate','count'):
            c=copy.deepcopy(self.capture)
            if mutate=='missing':c.raw=np.delete(c.raw,5,axis=0)
            if mutate=='duplicate':c.raw[1,0]=c.raw[0,0]
            if mutate=='saturate':c.raw[2,2]=8388607
            if mutate=='count':c.moments[0][0]+=1
            with self.subTest(mutate=mutate),self.assertRaises(ValueError):analyze(c,self.s,self.p)
    def test_weak_signal_is_not_valid(self):
        c=make_capture(self.s,self.p,noise_v=.02)
        r=analyze(c,self.s,self.p)
        self.assertFalse(r['quality_ok']);self.assertIn('voltage_weak_signal',r['reasons'])
    def test_actual_capture_binding_and_phase_are_checked(self):
        for field in ('configuration','adc_registers','phase'):
            c=copy.deepcopy(self.capture)
            if field=='phase':c.raw[2,1]+=1
            else:c.meta[field][0]^=2
            with self.subTest(field=field),self.assertRaises(ValueError):analyze(c,self.s,self.p)
    def test_zero_current(self):
        c=make_capture(self.s,self.p,drive=False,noise_v=0)
        r=analyze(c,self.s,self.p)
        self.assertFalse(r['valid']);self.assertIsNone(r['impedance_ohm'])
    def test_repeatability_requires_independent_windows(self):
        results=[analyze(make_capture(self.s,self.p,k),self.s,self.p) for k in (1,2,3)]
        self.assertEqual(repeatability(results)['count'],3)
        self.assertIsNone(repeatability(results[:1])['relative_std'])
    def test_rtl_raw_matches_every_hardware_moment(self):
        folder=ROOT/'build/sim'
        if not (folder/'rtl_raw.csv').exists():folder=ROOT.parents[1]/'sim/evidence/rtl'
        raw=np.loadtxt(folder/'rtl_raw.csv',delimiter=',',skiprows=1,dtype=np.int64)
        stats=np.loadtxt(folder/'rtl_stats.csv',delimiter=',',skiprows=1,dtype=np.int64)
        actual=moments_from_raw(raw,sine_lut())
        np.testing.assert_array_equal(actual[0],stats[:,1]);np.testing.assert_array_equal(actual[1],stats[:,2])
        # Shared-SPI DAC edges can defer a slot. Fit uses measured DRDY phase,
        # never a fabricated uniform 30 kS/s (or exact slot) time axis.
        gaps=np.diff(raw[:,0])
        self.assertGreaterEqual(gaps.min(),32768)
        self.assertLess(gaps.max(),35000)

class CalibrationTests(unittest.TestCase):
    def test_no_nominal_or_ab_as_transfer_calibration(self):
        data=reference();data['evidence_kind']='nominal_resistor'
        with self.assertRaises(ValueError):from_reference(data)
    def test_legacy_calibration_cannot_arm_final_assembly(self):
        document=from_reference(reference())
        binding=document['rows'][0]['binding'];binding.pop('assembly_sha256')
        binding.update(abi=0xbe01,r_shunt_ohm=10.,voltage_divider=2.)
        with tempfile.TemporaryDirectory() as out, self.assertRaisesRegex(ValueError,'exact binding'):
            MeasurementJob(SyntheticHardware(),profile(),Settings(),(80,),out,calibration=Calibration(document),source='simulation')
    def test_exact_hardware_rate_frequency_binding(self):
        s=Settings();p=profile();cal=Calibration(from_reference(reference(s)))
        for key,value in [('pga',2),('drate',0xe0),('board_serial','other'),('phase_increment',4295),('slot_cycles',33333)]:
            binding=p.binding(s);binding[key]=value
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'mismatch'):cal.corrections(binding,3.7,.01)
        with self.assertRaisesRegex(ValueError,'envelope'):cal.corrections(p.binding(s),1.5,.01)
    def test_ab_sign_and_moving_only_sense(self):
        base=dict(pair_id='pair',cell_id='cell',binding={'f':80},zero_ack_before_move=True,source='simulation')
        ra=dict(impedance_ohm=[.02,-.005],current_ac_a=[.02,0],voltage_dc_v=3.7,quality_ok=True,frequency_hz=80.)
        rb=dict(ra,impedance_ohm=[.03,-.005],voltage_dc_v=3.6998)
        a=dict(base,position='A',result=ra);b=dict(base,position='B',result=rb)
        standard=dict(independently_measured=True,reference_source='test-only',impedance_ohm=[.01,0],frequency_hz=80.,uncertainty_relative=.005)
        report=ab_check(a,b,standard)
        self.assertLess(report['relative_residual'],1e-12);self.assertFalse(report['creates_calibration'])
        for source in ('hardware',None,'unknown'):
            with self.subTest(source=source),self.assertRaisesRegex(ValueError,'source'):
                ab_check(a,dict(b,source=source),standard)
        with self.assertRaisesRegex(ValueError,'document differs'):
            ab_check(dict(a,calibration_document_sha256='a'),dict(b,calibration_document_sha256='b'),standard)
        with self.assertRaisesRegex(ValueError,'frequencies differ'):
            ab_check(a,dict(b,result=dict(rb,frequency_hz=81.)),standard)
        with self.assertRaisesRegex(ValueError,'independently measured'):
            ab_check(a,b,dict(standard,independently_measured='false'))
        b['zero_ack_before_move']=False
        with self.assertRaises(ValueError):ab_check(a,b,standard)
        b['zero_ack_before_move']=True;b['result']['current_ac_a']=[.025,0]
        with self.assertRaisesRegex(ValueError,'current changed'):ab_check(a,b,standard)

class WorkflowTests(unittest.TestCase):
    def test_bad_repeatability_invalidates_final_session(self):
        class Changing(SyntheticHardware):
            def start(self,settings,p,drive=True):
                super().start(settings,p,drive)
                if drive and self.sequence%2:
                    raw=self.capture.raw;mask=raw[:,3]==0;codes=raw[mask,2]
                    raw[mask,2]=np.rint(codes.mean()+3*(codes-codes.mean())).astype(np.int64)
                    self.capture.moments=moments_from_raw(raw,sine_lut())
        with tempfile.TemporaryDirectory() as out:
            job=MeasurementJob(Changing(),profile(),Settings(repeats=2),(80,),out,
                calibration=Calibration(from_reference(reference())),source='simulation')
            job.run()
            self.assertEqual(job.snapshot()['status'],'UNVERIFIED')
            self.assertTrue(all(not r['valid'] for r in job.results))
            self.assertTrue(all('repeatability_exceeds_5pct' in r['reasons'] for r in job.results))
    def test_ab_export_from_actual_saved_session(self):
        with tempfile.TemporaryDirectory() as out:
            job=MeasurementJob(SyntheticHardware(),profile(),Settings(repeats=2),(80,),out,
                metadata={'pair_id':'test-pair','zero_ack_before_move':True},source='simulation',position='A')
            job.run()
            result=export_ab_session(job.recorder.path,80)
            self.assertEqual(result['source'],'simulation');self.assertEqual(result['repeat_count'],2)
            self.assertEqual(result['position'],'A');self.assertEqual(len(result['summary_sha256']),64)
            self.assertTrue(result['raw_reconstruction_passed'])
            self.assertIn('001_raw.csv',result['evidence_sha256'])
            self.assertIsNone(result['result']['calibration_id'])
    def test_ab_export_rejects_modified_raw_even_when_summary_is_unchanged(self):
        with tempfile.TemporaryDirectory() as out:
            job=MeasurementJob(SyntheticHardware(),profile(),Settings(repeats=2),(80,),out,
                metadata={'pair_id':'test-pair','zero_ack_before_move':True},source='simulation',position='A')
            job.run()
            path=job.recorder.path/'001_raw.csv';rows=path.read_text().splitlines()
            fields=rows[2].split(',');fields[3]=str(int(fields[3])+1);rows[2]=','.join(fields)
            path.write_text('\n'.join(rows),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'PL moments'):export_ab_session(job.recorder.path,80)
    def test_whole_sweep_saves_raw_metadata_and_no_fake_labels(self):
        with tempfile.TemporaryDirectory() as out:
            h=SyntheticHardware();job=MeasurementJob(h,profile(),Settings(repeats=2),(20,80),out,source='simulation')
            job.run();state=job.snapshot()
            self.assertEqual(state['status'],'UNVERIFIED');self.assertEqual(state['completed'],4)
            root=Path(state['output']);summary=json.loads((root/'summary.json').read_text())
            self.assertEqual(len(list(root.glob('*_raw.csv'))),5)
            self.assertEqual(summary['source'],'simulation');self.assertIsNone(summary['ai']['rul'])
            self.assertIsNone(json.loads((root/'session.json').read_text())['context']['temperature_c'])
            self.assertFalse(h.active)
    def test_cancel_preserves_terminal_and_stops(self):
        with tempfile.TemporaryDirectory() as out:
            h=SyntheticHardware();job=MeasurementJob(h,profile(),Settings(),(80,),out,source='simulation')
            job.cancel();job.run()
            self.assertEqual(job.snapshot()['status'],'CANCELLED');self.assertFalse(h.active)
    def test_driver_error_still_stops(self):
        class Broken(SyntheticHardware):
            def poll(self):raise OSError('injected ADC failure')
        with tempfile.TemporaryDirectory() as out:
            h=Broken();job=MeasurementJob(h,profile(),Settings(),(80,),out,source='simulation');job.run()
            self.assertEqual(job.snapshot()['status'],'ERROR');self.assertFalse(h.active)
    def test_stop_failure_cannot_be_reported_success(self):
        class Broken(SyntheticHardware):
            def stop(self,timeout=.1):raise TimeoutError('injected zero failure')
        with tempfile.TemporaryDirectory() as out:
            job=MeasurementJob(Broken(),profile(),Settings(repeats=2),(80,),out,source='simulation');job.run()
            self.assertEqual(job.snapshot()['status'],'ERROR')

class PanelTests(unittest.TestCase):
    def test_all_states_pages_stay_in_frame_and_no_placeholder_glyphs(self):
        p=Painter()
        for state in ('IDLE','CAPTURING','DONE','ERROR','CANCELLED','TIMEOUT','UNVERIFIED'):
            for page in range(3):
                frame=p.render(dict(status=state,frequency_hz=80,result=None),page)
                self.assertEqual(frame.shape,(600,800,3))
                for x,y,x1,y1,text in p.bounds:
                    self.assertLessEqual(x1,800);self.assertLessEqual(y1,600);self.assertNotIn('?',text)
    def test_busy_controls_only_cancel_or_navigation(self):
        self.assertEqual(action((700,35),0,True),('cancel',None))
        self.assertIsNone(action((650,450),0,True))
        self.assertEqual(action((700,565),0,True),('page',2))
    def test_only_three_pages_exist(self):
        self.assertEqual(NAV,('首页','设置','记录'))
        self.assertEqual(len(NAV_RECTS),3)
        with self.assertRaises(ValueError):
            Painter().render(dict(status='IDLE',result=None),3)
    def test_settings_page_keeps_frequency_mode_and_contact_position(self):
        p=Painter()
        p.render(dict(status='IDLE',result=None),1,selected_hz=17.8,sweep=True,position='A')
        rendered=''.join(row[-1] for row in p.bounds)
        for label in ('设置','单点频率','17.8 Hz','测量模式','扫频','接法位置','位置 A'):
            self.assertIn(label,rendered)
        self.assertEqual(action((680,170),1,False),('frequency',1))
        self.assertEqual(action((500,170),1,False),('frequency',-1))
        self.assertEqual(action((200,310),1,False),('sweep',False))
        self.assertEqual(action((600,310),1,False),('sweep',True))
        self.assertEqual(action((100,398),1,False),('position','normal'))
        self.assertEqual(action((400,398),1,False),('position','A'))
        self.assertEqual(action((670,398),1,False),('position','B'))
        self.assertEqual(action((700,464),1,False),('start',None))
    def test_removed_pages_carry_no_navigation_targets(self):
        # The nav bar has three targets only; no coordinate can reach a fourth.
        for x in range(16,784,7):
            request=action((x,565),0,False)
            if request is not None:
                self.assertEqual(request[0],'page')
                self.assertIn(request[1],(0,1,2))

if __name__=='__main__':unittest.main(verbosity=2)
