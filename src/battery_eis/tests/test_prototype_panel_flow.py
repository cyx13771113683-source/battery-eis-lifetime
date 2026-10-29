"""Run real panel/job/recorder/model with replayed raw data and fake devices.

All created sessions are marked simulation; no board or physical touch is used.
"""
import json
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
PACKAGED=(ROOT/'application').is_dir()
sys.path.insert(0,str(ROOT/'application/ps' if PACKAGED else ROOT/'ps'))
import app
from configuration import HardwareProfile,Settings
from hardware import Capture
from panel import Painter
from prototype import PrototypeJob,PrototypeCalibration,PrototypeModel
from ui_records import RecordIndex,record_status_label,record_calibration_label
from synthetic import SyntheticHardware

GROUP=ROOT/'evidence/normal' if PACKAGED else ROOT.parents[1]/'data/hardware/normal'


class TestHardware(SyntheticHardware):
    def command(self,op,value):pass


class PrototypePanelFlowTests(unittest.TestCase):
    def test_touch_cancel_restart_complete_and_history_with_real_workflow(self):
        report=json.loads((GROUP/'report.json').read_text(encoding='utf-8'))
        profile=HardwareProfile(**report['original_profile'])
        calibration=PrototypeCalibration.load(ROOT/'prototype_calibration.initial.json' if PACKAGED else
                                              ROOT.parents[1]/'data/fixtures/prototype_calibration.json')
        model=PrototypeModel(ROOT/'application/models/lib_two_ridge.json' if PACKAGED else
                             ROOT/'models/frozen_two_frequency_20261022/lib_two_ridge.json')
        frames=[];jobs=[];handlers={};stage=[0];real_sleep=time.sleep
        class ReplayJob(PrototypeJob):
            def __init__(self,*args,**kwargs):
                kwargs['source']='simulation';super().__init__(*args,**kwargs)
                self.replay_index={};jobs.append(self)
            def capture(self,settings,drive):
                self.check()
                if self is jobs[0] and not drive:
                    # Give the UI a deterministic opportunity to cancel preflight.
                    while not self.cancel_event.wait(.005):self.check()
                    self.check()
                key=settings.frequency_hz if drive else 'zero'
                rows=[r for r in report['captures'] if (r['kind']=='ac' and r['settings']['frequency_hz']==key)
                      or (not drive and r['kind']=='zero')]
                index=self.replay_index.get(key,0);row=rows[index];self.replay_index[key]=index+1
                raw=np.loadtxt(GROUP/row['raw_file'],delimiter=',',skiprows=1,dtype=np.int64)
                doc=json.loads((GROUP/row['capture_file']).read_text())
                return Capture(raw,tuple(doc['moments']),dict(doc['meta'],source='simulation'))
        class Touch:
            just_pressed=True
            def poll(self,_):
                state=jobs[-1].snapshot()['status'] if jobs else None
                done=bool(jobs) and jobs[-1].thread is not None and not jobs[-1].thread.is_alive()
                point=None
                if stage[0]==0:point=(700,490);stage[0]=1
                elif stage[0]==1 and state=='PREFLIGHT':point=(700,30);stage[0]=2
                elif stage[0]==2 and state=='CANCELLED' and done and frames[-1]['status']=='CANCELLED':point=(700,490);stage[0]=3
                elif stage[0]==3 and state=='DONE' and done and frames[-1]['status']=='DONE':point=(660,565);stage[0]=4
                elif stage[0]==4:
                    rows=RecordIndex(directory).page(0)
                    index=next(i for i,r in enumerate(rows) if r['status']=='DONE')
                    point=(100,170+72*index);stage[0]=5
                elif stage[0]==5:point=(700,490);stage[0]=6
                elif stage[0]==6:point=(700,490);stage[0]=7
                elif stage[0]==7:point=(700,490);stage[0]=8
                elif stage[0]==8 and state=='DONE' and done:
                    stage[0]=9;next(iter(handlers.values()))(None,None)
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
            def writeframe(self,frame):
                real_sleep(.002)
                if len(frames)>700:raise TimeoutError('offline UI replay did not terminate')
        render_original=Painter.render
        def render(painter,state,*args,**kwargs):
            frames.append(dict(status=state['status'],page=args[0],record=kwargs.get('record') is not None,
                               soh=state.get('ai',{}).get('soh')))
            return render_original(painter,state,*args,**kwargs)
        video=types.ModuleType('pynq.lib.video');video.VideoMode=lambda *args:args
        touch=types.ModuleType('linux_touch');touch.TouchReader=TouchReader
        with tempfile.TemporaryDirectory(prefix='eis-full-ui-replay-') as directory:
            hardware=TestHardware();hardware.original_profile=profile
            overlay=types.SimpleNamespace(video=types.SimpleNamespace(hdmi_out=Hdmi(),axi_vdma=None))
            args=types.SimpleNamespace(output=Path(directory),sweep=[80.,17.8,2.16],position='normal',
                pair_id=None,confirm_zero_before_move=False,budget=20.,prototype_mode=True)
            with patch.dict(sys.modules,{'pynq':types.ModuleType('pynq'),'pynq.lib':types.ModuleType('pynq.lib'),
                'pynq.lib.video':video,'linux_touch':touch}),patch.object(app,'MeasurementJob',ReplayJob),\
                patch.object(app.signal,'signal',lambda s,f:handlers.update({s:f})),\
                patch.object(app.time,'sleep',lambda _:None),patch.object(Painter,'render',render),\
                patch('builtins.print') as output:
                app.panel_loop(hardware,overlay,args,profile,Settings(),calibration,{},model)
            frame_ready=[c for c in output.call_args_list if c.args[0]=='UI_FRAME_READY pages=3']
            shown=[c.args for c in output.call_args_list if c.args[0]=='UI_RESULT']
            self.assertEqual(len(frame_ready),1)
            self.assertEqual([r[1] for r in shown],['CANCELLED','DONE','DONE'])
            self.assertTrue(all(r[3]=='source=simulation' for r in shown))
            self.assertEqual(stage[0],9)
            self.assertEqual(len(jobs),3)
            self.assertEqual([j.snapshot()['status'] for j in jobs],['CANCELLED','DONE','DONE'])
            self.assertFalse(any(j.thread.is_alive() for j in jobs))
            self.assertIsNone(jobs[0].snapshot()['ai']['soh'])
            for job in jobs[1:]:
                self.assertAlmostEqual(job.snapshot()['ai']['soh'],.7627624428616855,places=10)
                self.assertEqual(len(job.results),6)
                summary=json.loads((job.recorder.path/'summary.json').read_text())
                self.assertEqual(summary['source'],'simulation')
                self.assertEqual(summary['status'],'DONE')
                self.assertFalse(summary['ai']['valid_for_board'])
                self.assertEqual(len(list(job.recorder.path.glob('*_raw.csv'))),7)
            records=RecordIndex(directory).page(0)
            self.assertEqual(len(records),3)
            done_rows=[r for r in records if r['status']=='DONE']
            self.assertEqual(len(done_rows),2)
            for row in done_rows:
                self.assertEqual(record_status_label(row),'测量完成')
                self.assertEqual(record_calibration_label(row),'已校正')
                self.assertFalse(row['measurement_valid'])
            self.assertTrue(any(f['record'] for f in frames))
            self.assertTrue(any(f['page']==2 for f in frames))

    def test_record_labels_keep_formal_validity_separate(self):
        self.assertEqual(record_status_label(dict(measurement_valid=True)),'记录有效')
        self.assertEqual(record_calibration_label(dict(calibrated=True)),'已标定')
        for status,label in [('CANCELLED','已取消'),('ERROR','测量错误'),('TIMEOUT','采集超时')]:
            self.assertEqual(record_status_label(dict(prototype_mode=True,status=status)),label)
        self.assertEqual(record_status_label(dict(prototype_mode=True,status='DONE',ratio_corrected=True,
                         spectrum=[dict(quality_ok=False)])),'待核验')
        record=dict(prototype_mode=True,status='DONE',ratio_corrected=True,spectrum=[dict(quality_ok=True)],
                    ai=dict(status='prototype_result',soh=.763),utc='2026-10-28T00:00:00Z',cell_id='REPLAY',
                    result=None,source='simulation')
        painter=Painter();painter.render(dict(status='IDLE'),2,history=dict(rows=[record]))
        self.assertTrue(any(bound[-1]=='76.3%' for bound in painter.bounds),'SOH percent must fit the list column')


if __name__=='__main__':unittest.main(verbosity=2)
