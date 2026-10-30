"""Host-only functional replay of the three-page operator interface.

Drives the real ps/app.py panel loop with fake video, fake touch and fake
hardware. No ADC/DAC, no display device, no measurement is created, and every
record used here is a simulated display fixture.
"""
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import numpy as np

HERE=Path(__file__).resolve().parent
BASE=HERE.parent/'src/battery_eis'
sys.path[:0]=[str(BASE/'ps'),str(HERE)]
from panel import Painter,action,NAV,NAV_RECTS
from ui_records import RecordIndex,RecordBrowser,spectrum_points,health_values
from preview import write_demo,demo_record
import app


class ThreePageUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='eis-three-pages-',dir=HERE)
        cls.folder=Path(cls.tmp.name).resolve()
        cls.data=cls.folder/'records';write_demo(cls.data,13)
        # Explicit display fixtures need an SOH to appear in operator history.
        for path in cls.data.glob('*/summary.json'):
            summary=json.loads(path.read_text(encoding='utf-8'))
            summary['ai']=dict(status='prototype_result',soh=.763)
            path.write_text(json.dumps(summary),encoding='utf-8')
        cls.painter=Painter()

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def state(self):
        demo=demo_record()
        return dict(status='UNVERIFIED',source='simulation',ai=demo['ai'],result=demo['results'][-1],spectrum=demo['results'])

    def test_only_three_pages_render_in_every_status(self):
        browser=RecordBrowser(self.data)
        self.assertEqual(NAV,('首页','设置','记录'))
        for status in ('IDLE','INITIALIZING','PREFLIGHT','CAPTURING','ANALYZING','DONE','UNVERIFIED','ERROR','CANCELLED','TIMEOUT'):
            for page in range(3):
                frame=self.painter.render(dict(self.state(),status=status),page=page,history=browser.view())
                self.assertEqual(frame.shape,(600,800,3))
                for x,y,x1,y1,text in self.painter.bounds:
                    self.assertTrue(0<=x<=x1<=800 and 0<=y<=y1<=600)
                    self.assertNotIn('?',text)
        for page in (3,4,5):
            with self.assertRaises(ValueError):
                self.painter.render(self.state(),page=page)

    def test_touch_regions_cover_the_three_pages(self):
        for index,(x,y,x1,y1) in enumerate(NAV_RECTS):
            self.assertGreaterEqual(x1-x,44);self.assertGreaterEqual(y1-y,44)
            self.assertEqual(action(((x+x1)//2,565),0,True),('page',index))
        self.assertEqual(action((700,35),0,True),('cancel',None))
        self.assertEqual(action((700,490),0,False),('start',None))
        self.assertEqual(action((700,464),1,False),('start',None))
        self.assertEqual(action((500,170),1,False),('frequency',-1))
        self.assertEqual(action((680,170),1,False),('frequency',1))
        self.assertEqual(action((200,310),1,False),('sweep',False))
        self.assertEqual(action((600,310),1,False),('sweep',True))
        self.assertEqual(action((100,398),1,False),('position','normal'))
        self.assertEqual(action((400,398),1,False),('position','A'))
        self.assertEqual(action((670,398),1,False),('position','B'))
        self.assertEqual(action((700,490),2,True),('record_page',1))
        self.assertEqual(action((100,170),2,True),('record_open',0))
        self.assertEqual(action((80,42),2,True,record_detail=True),('record_back',None))
        self.assertIsNone(action((700,490),2,True,record_detail=True))
        self.assertIsNone(action((80,565),2,True,record_detail=True))
        self.assertEqual(action((700,490),0,True,viewing_record=True),('return_live',None))

    def test_removed_pages_reach_no_render_and_no_action(self):
        for page in (3,4):
            with self.assertRaises(ValueError):
                self.painter.render(self.state(),page=page)
        # The old five-button nav had targets at 390 and 550; now they are
        # 设置 and 记录, and no coordinate can produce a page index above 2.
        for x in range(0,800,5):
            request=action((x,565),0,False)
            if request is not None and request[0]=='page':
                self.assertIn(request[1],(0,1,2))

    def test_panel_loop_navigates_all_three_pages_and_blocks_a_bad_start(self):
        steps=[((400,565),dict(page=1)),((680,170),dict(selected=17.8)),((200,310),dict(sweep=False)),
               ((600,310),dict(sweep=True)),((400,398),dict(position='A')),
               ((700,464),dict(status='ERROR',notice=True)),((400,380),dict(notice=False)),
               ((660,565),dict(page=2)),((100,170),dict(detail=True)),((80,42),dict(detail=False)),
               ((700,490),dict(history_page=1)),((100,490),dict(history_page=0)),
               ((100,170),dict(detail=True)),((80,42),dict(detail=False)),((80,565),dict(page=0))]
        events=[step[0] for step in steps]
        handlers={};views=[];checked=[]
        class Touch:
            just_pressed=True
            def poll(self,_):return (events.pop(0),True) if events else (None,False)
            def close(self):pass
        class TouchReader:
            @staticmethod
            def autodetect(*args):return Touch()
        class Hdmi:
            _vdma=True;frames=0
            def configure(self,*args):pass
            def start(self):pass
            def stop(self):pass
            def newframe(self):return np.zeros((600,800,3),dtype=np.uint8)
            def writeframe(self,frame):
                self.frames+=1
                if self.frames>=len(steps):next(iter(handlers.values()))(None,None)
        class Hardware:
            stops=0
            def stop(self):self.stops+=1
        hdmi=Hdmi();hardware=Hardware()
        overlay=types.SimpleNamespace(video=types.SimpleNamespace(hdmi_out=hdmi,axi_vdma=None))
        args=types.SimpleNamespace(output=self.data,sweep=[2.16,17.8],position='normal',pair_id=None,
            confirm_zero_before_move=False,budget=20.)
        model=types.SimpleNamespace(reject=lambda _: {})
        original_render=Painter.render
        def render(painter,state,*args,**kwargs):
            history=kwargs.get('history') or {}
            views.append(dict(page=args[0] if args else kwargs.get('page',0),
                selected=args[1] if len(args)>1 else None,sweep=args[2] if len(args)>2 else None,
                position=args[3] if len(args)>3 else None,status=state.get('status'),
                error=state.get('error'),notice=bool(state.get('ui_notice')),
                history_page=history.get('page'),detail=history.get('selected') is not None,
                historical=kwargs.get('record') is not None))
            return original_render(painter,state,*args,**kwargs)
        fake_video=types.ModuleType('pynq.lib.video');fake_video.VideoMode=lambda *args:args
        fake_touch=types.ModuleType('linux_touch');fake_touch.TouchReader=TouchReader
        with (patch.dict(sys.modules,{'pynq':types.ModuleType('pynq'),'pynq.lib':types.ModuleType('pynq.lib'),
                'pynq.lib.video':fake_video,'linux_touch':fake_touch}),patch.object(app,'MeasurementJob') as job,
                patch.object(app.signal,'signal',side_effect=lambda signum,handler:handlers.update({signum:handler})),
                patch.object(app.time,'sleep',lambda _:None),patch.object(Painter,'render',render)):
            app.panel_loop(hardware,overlay,args,None,None,None,{},model)
        # No measurement job may be constructed: the A/B guard refuses first.
        job.assert_not_called()
        self.assertEqual(hardware.stops,2)
        index=0
        for point,expected in steps:
            if index>=len(views):break
            view=views[index]
            for key,value in expected.items():
                actual=view.get(key)
                if key=='error':self.assertIn('A/B requires',actual or '')
                else:self.assertEqual(actual,value,(index,key,value,actual))
            index+=1
        self.assertEqual(index,len(steps),'every scripted step must be observed')
        self.assertGreaterEqual(hdmi.frames,len(steps))

    def test_records_browse_without_creating_a_measurement(self):
        events=[(660,565),(100,170),(80,42),(80,565)]
        handlers={}
        class Touch:
            just_pressed=True
            def poll(self,_):return (events.pop(0),True) if events else (None,False)
            def close(self):pass
        class TouchReader:
            @staticmethod
            def autodetect(*args):return Touch()
        class Hdmi:
            _vdma=True;frames=0
            def configure(self,*args):pass
            def start(self):pass
            def stop(self):pass
            def newframe(self):return np.zeros((600,800,3),dtype=np.uint8)
            def writeframe(self,frame):
                self.frames+=1
                if self.frames==6:next(iter(handlers.values()))(None,None)
        class Hardware:
            stops=0
            def stop(self):self.stops+=1
        hdmi=Hdmi();hardware=Hardware()
        overlay=types.SimpleNamespace(video=types.SimpleNamespace(hdmi_out=hdmi,axi_vdma=None))
        args=types.SimpleNamespace(output=self.data,sweep=[2.16,17.8],position='normal',pair_id=None,
            confirm_zero_before_move=False,budget=20.)
        model=types.SimpleNamespace(reject=lambda _: {})
        fake_video=types.ModuleType('pynq.lib.video');fake_video.VideoMode=lambda *args:args
        fake_touch=types.ModuleType('linux_touch');fake_touch.TouchReader=TouchReader
        with (patch.dict(sys.modules,{'pynq':types.ModuleType('pynq'),'pynq.lib':types.ModuleType('pynq.lib'),
                'pynq.lib.video':fake_video,'linux_touch':fake_touch}),patch.object(app,'MeasurementJob') as job,
                patch.object(app.signal,'signal',side_effect=lambda signum,handler:handlers.update({signum:handler})),
                patch.object(app.time,'sleep',lambda _:None)):
            app.panel_loop(hardware,overlay,args,None,None,None,{},model)
        job.assert_not_called();self.assertEqual(hardware.stops,1);self.assertEqual(hdmi.frames,6)

    def test_broken_record_is_pending_and_never_a_crash(self):
        bad=self.folder/'bad';bad.mkdir(exist_ok=True)
        (bad/'session.json').write_text('{}')
        (bad/'summary.json').write_text('{broken')
        record=RecordIndex(self.folder).load(bad)
        self.assertTrue(record['read_error'])
        frame=self.painter.render(self.state(),2,history={'selected':record})
        self.assertEqual(frame.shape,(600,800,3))
        record.update(result={'voltage_dc_v':'bad','current_dc_a':None},spectrum=[])
        self.painter.render(self.state(),record=record)

    def test_spectrum_average_and_health_gating_still_hold(self):
        rows=[dict(frequency_hz=10.,impedance_ohm=[-1.,.01],quality_ok=True,valid=True),
              dict(frequency_hz=10.,impedance_ohm=[-1.,-.01],quality_ok=True,valid=True)]
        points=spectrum_points(rows)
        self.assertEqual(len(points),1)
        self.assertAlmostEqual(abs(points[0]['phase_deg']),180)
        for status in ('rejected','no_validated_battery_model','unknown'):
            display=health_values(dict(status=status,soh=.9,capacity_Ah=3,rul=200,rul_unit='cycles'))
            self.assertTrue(all('待核验' in value for value in display.values()))


if __name__=='__main__':unittest.main(verbosity=2)
