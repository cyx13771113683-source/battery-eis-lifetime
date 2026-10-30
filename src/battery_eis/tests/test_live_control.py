"""Deterministic worker scheduling and retained three-frequency display tests."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import time
import types
from unittest.mock import patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ps'))
from live_control import LiveController,RetainedSpectrum
from panel import Painter,action

FREQUENCIES=(80.,17.8,2.16)

def rows(f,z=.01):
    return [dict(requested_frequency_hz=f,frequency_hz=f,impedance_ohm=[z,-.001],
                 repeat=r,quality_ok=True,prototype_calibration_id='unchanged',
                 magnitude_ohm=z,phase_deg=0) for r in (0,1)]

class Model:
    @staticmethod
    def reject(reason):return dict(status='rejected',reason=reason,soh=None)

class Worker:
    def __init__(self,frequencies):
        self.frequencies=tuple(frequencies);self.results=[];self.cancelled=False;self.active=False
        self.thread=self;self.state=dict(status='IDLE',output=str(id(self)),result=None,ai=Model.reject('spectrum_incomplete'))
    def start(self):self.active=True;self.state['status']='CAPTURING'
    def cancel(self):self.cancelled=True;self.state['status']='CANCELLING'
    def is_alive(self):return self.active
    def snapshot(self):return dict(self.state)
    def complete(self,status='DONE',alive=False):
        self.active=alive;self.state['status']=status
        if status=='DONE':
            self.results=[r for f in self.frequencies for r in rows(f)]
            if len(self.frequencies)==3:self.state['ai']=dict(status='prototype_result',soh=.763)

class LiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.jobs=[]
        def factory(f):
            self.assertFalse(any(j.active for j in self.jobs),'workers overlap')
            job=Worker(f);self.jobs.append(job);return job
        self.control=LiveController(factory,Model,self.temp.name,FREQUENCIES,17.8)

    def test_continuous_single_repeats_without_producing_lifetime(self):
        c=self.control;c.toggle_single();c.tick();self.jobs[0].complete();c.tick()
        self.assertEqual(len(self.jobs),2);self.assertEqual(self.jobs[1].frequencies,(17.8,))
        self.assertIsNone(c.snapshot()['ai']['soh']);self.assertEqual(len(c.snapshot()['spectrum']),1)

    def test_lifetime_waits_for_old_worker_and_resumes_single(self):
        c=self.control;c.toggle_single();c.tick();c.request_lifetime();c.tick()
        self.assertTrue(self.jobs[0].cancelled);self.assertEqual(len(self.jobs),1)
        self.jobs[0].complete('CANCELLED',alive=True);c.tick();self.assertEqual(len(self.jobs),1)
        self.jobs[0].active=False;c.tick();self.assertEqual(self.jobs[1].frequencies,FREQUENCIES)
        self.jobs[1].complete();c.tick()
        self.assertEqual(self.jobs[2].frequencies,(17.8,));self.assertTrue(c.single_running)
        self.assertEqual(c.snapshot()['ai']['soh'],.763);self.assertEqual(len(c.snapshot()['spectrum']),3)
        self.assertEqual(c.last_life['status'],'DONE')
        self.jobs[2].complete();c.tick();self.assertEqual(c.snapshot()['ai']['soh'],.763)

    def test_stopped_before_lifetime_remains_stopped(self):
        c=self.control;c.request_lifetime();c.tick();self.jobs[0].complete();c.tick()
        self.assertIsNone(c.job);self.assertFalse(c.single_running);self.assertEqual(len(self.jobs),1)

    def test_80hz_resumes_and_voltage_current_stay_visible_between_jobs(self):
        c=self.control;c.select(80.);c.toggle_single();c.tick()
        result=dict(voltage_dc_v=3.44,current_dc_a=.2,frequency_hz=80.)
        c.job.state['result']=result;c.job.complete();c.tick()
        self.assertEqual(c.snapshot()['result'],result)
        self.assertEqual(c.job.frequencies,(80.,));self.assertIsNone(c.snapshot()['ai']['soh'])
        c.request_lifetime();c.job.complete('CANCELLED');c.tick()
        self.assertEqual(c.job.frequencies,FREQUENCIES)
        c.select(17.8);self.assertEqual(c.selected,80.)
        c.job.complete();c.tick()
        self.assertEqual(c.job.frequencies,(80.,));self.assertTrue(c.single_running)
        self.assertEqual(c.snapshot()['ai']['soh'],.763)

    def test_change_frequency_waits_then_restarts_and_stop_preserves_chart(self):
        c=self.control;c.toggle_single();c.tick();self.jobs[0].complete();c.tick()
        c.select(80.);c.tick();self.assertTrue(self.jobs[1].cancelled)
        self.jobs[1].complete('CANCELLED');c.tick();self.assertEqual(c.job.frequencies,(80.,))
        c.toggle_single();c.job.complete('CANCELLED');c.tick()
        self.assertIsNone(c.job);self.assertEqual(len(c.cache.rows()),1)

    def test_cancel_lifetime_does_not_resume_and_errors_stop(self):
        c=self.control;c.toggle_single();c.tick();c.request_lifetime()
        self.jobs[0].complete('CANCELLED');c.tick();c.cancel();self.jobs[1].complete('CANCELLED');c.tick()
        self.assertIsNone(c.job);self.assertFalse(c.single_running)
        c.toggle_single();c.tick();c.job.complete('ERROR');c.tick();self.assertIsNone(c.job)

    def test_lifetime_failure_never_restarts_drive(self):
        c=self.control;c.toggle_single();c.tick();c.request_lifetime()
        self.jobs[0].complete('CANCELLED');c.tick();c.job.complete('TIMEOUT');c.tick()
        self.assertFalse(c.single_running);self.assertIsNone(c.job);self.assertIsNone(c.snapshot()['ai']['soh'])

    def test_bad_constructor_surfaces_error(self):
        def fail(f):raise ValueError('fixture not ready')
        c=self.control;c.factory=fail;c.toggle_single();c.tick()
        self.assertEqual(c.snapshot()['status'],'ERROR');self.assertFalse(c.single_running)

    def test_display_cache_updates_only_one_frequency_and_reloads(self):
        c=self.control
        for f in FREQUENCIES:c.cache.ingest(rows(f),'old')
        before=c.cache.rows();c.cache.ingest(rows(17.8,.02),'new');after=c.cache.rows()
        self.assertEqual([p['requested_frequency_hz'] for p in after],[2.16,17.8,80.])
        self.assertEqual(before[0],after[0]);self.assertEqual(before[2],after[2])
        self.assertEqual(after[1]['impedance_ohm'][0],.02)
        self.assertEqual(RetainedSpectrum(self.temp.name,FREQUENCIES).rows(),after)
        c.request_lifetime();c.tick();self.assertEqual(c.job.results,[])
        self.assertIsNone(c.snapshot()['ai']['soh'])

    def test_partial_bad_repeats_and_unknown_frequencies_do_not_replace(self):
        cache=self.control.cache;cache.ingest(rows(80.),'old');before=cache.rows()
        for bad in (rows(80.,.02)[:1],rows(9.),[rows(80.)[0]]*2,
                    [dict(r,quality_ok=False) for r in rows(80.)]):cache.ingest(bad,'bad')
        self.assertEqual(cache.rows(),before)

    def test_single_control_hitbox_and_no_hidden_sweep_mode(self):
        self.assertEqual(action((590,283),1,False,live_controls=True),('toggle_single',None))
        self.assertEqual(action((700,490),0,True,live_controls=True),('lifetime',None))
        self.assertIsNone(action((700,490),1,False,live_controls=True))
        self.assertIsNone(action((590,283),1,True,live_controls=True,lifetime_running=True))
        painter=Painter();painter.render(dict(status='IDLE',live_controls=True),1)
        labels=[r[-1] for r in painter.bounds]
        self.assertIn('开始单点',labels);self.assertNotIn('扫频',labels);self.assertNotIn('开始测量',labels)

    def test_real_panel_routes_buttons_resumes_and_skips_unchanged_frames(self):
        import app
        jobs=[];frames=[];handlers={};stage=[0];polls=[0];idle_frames=[]
        class Job(Worker):
            def __init__(self,h,p,s,frequencies,*args,**kwargs):
                if any(j.active for j in jobs):raise AssertionError('overlapping jobs')
                super().__init__(frequencies);jobs.append(self)
            def join(self,timeout=None):self.active=False
        class Touch:
            just_pressed=True
            def poll(self,_):
                polls[0]+=1
                if polls[0]>150:raise AssertionError('live panel stalled')
                point=None
                if stage[0]==0:point=(380,565);stage[0]=1
                elif stage[0]==1:point=(590,283);stage[0]=2
                elif stage[0]==2 and len(jobs)==1:point=(100,565);stage[0]=3
                elif stage[0]==3:point=(700,490);stage[0]=4
                elif stage[0]==4:
                    if not jobs[0].cancelled:raise AssertionError('single was not preempted')
                    jobs[0].complete('CANCELLED');stage[0]=5
                elif stage[0]==5 and len(jobs)==2:jobs[1].complete();stage[0]=6
                elif stage[0]==6 and len(jobs)==3:point=(380,565);stage[0]=7
                elif stage[0]==7:point=(590,283);stage[0]=8
                elif stage[0]==8:jobs[2].complete('CANCELLED');stage[0]=9
                elif stage[0]>=9:
                    idle_frames.append(len(frames));stage[0]+=1
                    if stage[0]==30:next(iter(handlers.values()))(None,None)
                return point,point is not None
            def close(self):pass
        class TouchReader:
            @staticmethod
            def autodetect(*args):return Touch()
        class Hdmi:
            _vdma=True
            def configure(self,*args):pass
            def start(self):pass
            def stop(self):pass
            def newframe(self):return np.zeros((600,800,3),dtype=np.uint8)
            def writeframe(self,frame):pass
        original=Painter.render
        def render(painter,state,*args,**kwargs):
            frames.append(dict(state));return original(painter,state,*args,**kwargs)
        video=types.ModuleType('pynq.lib.video');video.VideoMode=lambda *args:args
        touch=types.ModuleType('linux_touch');touch.TouchReader=TouchReader
        args=types.SimpleNamespace(output=Path(self.temp.name),sweep=list(FREQUENCIES),position='normal',
            pair_id=None,confirm_zero_before_move=False,budget=20.,prototype_mode=True,live_controls=True)
        overlay=types.SimpleNamespace(video=types.SimpleNamespace(hdmi_out=Hdmi(),axi_vdma=None))
        with patch.dict(sys.modules,{'pynq':types.ModuleType('pynq'),'pynq.lib':types.ModuleType('pynq.lib'),
             'pynq.lib.video':video,'linux_touch':touch}),patch.object(app,'MeasurementJob',Job),\
             patch.object(app.signal,'signal',lambda s,f:handlers.update({s:f})),\
             patch.object(app.time,'sleep',lambda _:None),patch.object(Painter,'render',render),patch('builtins.print'):
            app.panel_loop(types.SimpleNamespace(stop=lambda:None),overlay,args,None,None,None,{},Model)
        self.assertEqual([j.frequencies for j in jobs],[(80.,),FREQUENCIES,(80.,)])
        self.assertFalse(any(j.active for j in jobs));self.assertEqual(len(set(idle_frames[2:])),1)
        self.assertEqual(len(frames[-1]['spectrum']),3);self.assertEqual(frames[-1]['ai']['soh'],.763)

if __name__=='__main__':unittest.main(verbosity=2)
