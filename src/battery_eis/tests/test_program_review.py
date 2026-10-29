"""Regression coverage for runtime binding and failed touch-screen starts."""
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE/'ps'))
import app
from configuration import HardwareProfile
from panel import Painter

spec=importlib.util.spec_from_file_location('review_launcher',BASE/'scripts/prototype_start.py')
launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)


class RuntimeBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='eis-runtime-binding-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.runtime=self.root/'runtime';self.runtime.mkdir()
        self.frozen=self.root/'program/battery_eis_rev_a/release/be03_offline_20261023_final'
        self.frozen.mkdir(parents=True)
        self.profile=HardwareProfile.load(BASE.parents[1]/'data/fixtures/profile.pending.json')
        files={}
        for name in ('battery_eis.bit','battery_eis.hwh'):
            payload=('test-fixture-'+name).encode();(self.runtime/name).write_bytes(payload)
            files[name]=hashlib.sha256(payload).hexdigest()
        self.profile=replace(self.profile,overlay_sha256=files['battery_eis.bit'])
        manifest=dict(abi=0xbe03,frontend_sha256=self.profile.frontend_sha256,
                      assembly_sha256=self.profile.assembly_sha256,files=files)
        for folder in (self.runtime,self.frozen):(folder/'manifest.json').write_text(json.dumps(manifest))

    def test_original_runtime_matches_profile_and_release(self):
        launcher.verify_runtime_binding(self.root,self.profile)

    def test_changed_runtime_even_with_updated_manifest_is_rejected(self):
        path=self.runtime/'battery_eis.bit';path.write_bytes(b'changed')
        manifest=json.loads((self.runtime/'manifest.json').read_text())
        manifest['files'][path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
        (self.runtime/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(RuntimeError,'runtime/release binding'):
            launcher.verify_runtime_binding(self.root,self.profile)

    def test_wrong_profile_bit_hash_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'overlay hash mismatch'):
            launcher.verify_runtime_binding(self.root,replace(self.profile,overlay_sha256='0'*64))

    def test_wrong_frontend_metadata_is_rejected(self):
        manifest=json.loads((self.runtime/'manifest.json').read_text());manifest['frontend_sha256']='0'*64
        (self.runtime/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(RuntimeError,'frontend_sha256'):
            launcher.verify_runtime_binding(self.root,self.profile)


class TouchStartFailureTests(unittest.TestCase):
    def replay(self,stop_failure=False):
        frames=[];handlers={};calls=[];closed=[];frame_count=[0]
        class PreviousJob:
            thread=None;results=[]
            def start(self):pass
            def snapshot(self):
                return dict(status='DONE',source='simulation',output='previous-session',result=None,
                            ai=dict(status='prototype_result',soh=.76,capacity_Ah=None,rul=None))
        class Hardware:
            def stop(self):
                if stop_failure and frame_count[0]==2:raise TimeoutError('stop acknowledgement')
        class Touch:
            just_pressed=True
            def poll(self,_):return ((700,490),True) if frame_count[0] in (0,2) else (None,False)
            def close(self):closed.append(True)
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
                frame_count[0]+=1
                if frame_count[0]>=5:next(iter(handlers.values()))(None,None)
        def factory(*args,**kwargs):
            calls.append(True)
            if len(calls)==2:raise OSError('disk full during session creation')
            return PreviousJob()
        def render(painter,state,*args,**kwargs):
            frames.append(dict(status=state['status'],soh=state.get('ai',{}).get('soh'),error=state.get('error'),
                               notice=bool(state.get('ui_notice'))))
            return np.zeros((600,800,3),dtype=np.uint8)
        video=types.ModuleType('pynq.lib.video');video.VideoMode=lambda *args:args
        touch=types.ModuleType('linux_touch');touch.TouchReader=TouchReader
        with tempfile.TemporaryDirectory(prefix='eis-touch-start-') as d:
            args=types.SimpleNamespace(output=Path(d),sweep=[80.,17.8,2.16],position='normal',pair_id=None,
                                      confirm_zero_before_move=False,budget=20.,prototype_mode=True)
            overlay=types.SimpleNamespace(video=types.SimpleNamespace(hdmi_out=Hdmi(),axi_vdma=None))
            with patch.dict(sys.modules,{'pynq':types.ModuleType('pynq'),'pynq.lib':types.ModuleType('pynq.lib'),
                'pynq.lib.video':video,'linux_touch':touch}),patch.object(app,'MeasurementJob',factory),\
                patch.object(app.signal,'signal',lambda s,f:handlers.update({s:f})),\
                patch.object(app.time,'sleep',lambda _:None),patch.object(Painter,'render',render):
                app.panel_loop(Hardware(),overlay,args,None,None,None,{},
                               types.SimpleNamespace(reject=lambda reason:dict(status='no_result',soh=None,reason=reason)))
        return frames,closed

    def test_session_creation_io_error_clears_old_soh_and_shows_failure(self):
        frames,closed=self.replay()
        self.assertEqual(frames[-1]['status'],'ERROR');self.assertIsNone(frames[-1]['soh'])
        self.assertIn('disk full',frames[-1]['error']);self.assertTrue(frames[-1]['notice'])
        self.assertEqual(len(closed),1,'touch should close only at normal loop cleanup')

    def test_stop_timeout_is_shown_in_ui_instead_of_leaving_the_loop(self):
        frames,_=self.replay(stop_failure=True)
        self.assertEqual(frames[-1]['status'],'ERROR');self.assertIsNone(frames[-1]['soh'])
        self.assertIn('stop acknowledgement',frames[-1]['error'])


if __name__=='__main__':unittest.main(verbosity=2)
