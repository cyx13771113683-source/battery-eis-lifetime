"""Three-page battery prototype launcher, one-shot test and computer maintenance."""
import argparse
import atexit
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

HERE=Path(__file__).resolve().parent
if HERE.name=='scripts':HERE=HERE.parent
sys.path.insert(0,str(HERE/'ps'))
from configuration import HardwareProfile,Settings
from prototype import PrototypeCalibration,PrototypeModel,PrototypeJob,FastHardware


class RunLog:
    """Mirror the current process output without removing system journal evidence."""
    def __init__(self,stream,log):self.stream=stream;self.log=log
    def write(self,text):
        result=self.stream.write(text);self.log.write(text);return result
    def flush(self):self.stream.flush();self.log.flush()
    def __getattr__(self,name):return getattr(self.stream,name)


def start_current_log(root):
    path=Path(root)/'runtime.current.log'
    log=path.open('w',encoding='utf-8',buffering=1)
    stdout,stderr=sys.stdout,sys.stderr
    sys.stdout=RunLog(stdout,log);sys.stderr=RunLog(stderr,log)
    def close_log():
        sys.stdout=stdout;sys.stderr=stderr;log.close()
    atexit.register(close_log)
    print('RUN_START current process only',flush=True)
    return path


def verify_runtime_binding(root,profile):
    """Bind the actual BIT/HWH pair to the profile and frozen release."""
    root=Path(root)
    runtime=json.loads((root/'runtime/manifest.json').read_text())
    frozen=json.loads((root/'program/battery_eis_rev_a/release/be03_offline_20261023_final/manifest.json').read_text())
    for key,expected in [('abi',0xbe03),('frontend_sha256',profile.frontend_sha256),
                         ('assembly_sha256',profile.assembly_sha256)]:
        if runtime.get(key)!=expected or frozen.get(key)!=expected:
            raise RuntimeError('runtime/profile binding: '+key)
    for name in ('battery_eis.bit','battery_eis.hwh'):
        actual=hashlib.sha256((root/'runtime'/name).read_bytes()).hexdigest()
        if actual!=runtime['files'][name] or actual!=frozen['files'][name]:
            raise RuntimeError('runtime/release binding: '+name)
        if name=='battery_eis.bit' and actual!=profile.overlay_sha256:
            raise RuntimeError('runtime/profile overlay hash mismatch')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=HERE.parent)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--reuse-overlay',action='store_true')
    parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args();root=args.root.resolve()
    if not args.verify_only:start_current_log(root)
    manifest_path=HERE/'manifest.json'
    if manifest_path.exists():
        manifest=json.loads(manifest_path.read_text())
        for name,expected in manifest['files'].items():
            if hashlib.sha256((HERE/name).read_bytes()).hexdigest()!=expected:raise RuntimeError('package hash: '+name)
    frozen=root/'program/battery_eis_rev_a'
    source=json.loads((frozen/'release/be03_offline_20261023_final/source_manifest.json').read_text())
    for name,expected in source.items():
        if hashlib.sha256((frozen/name).read_bytes()).hexdigest()!=expected:raise RuntimeError('base hash: '+name)
    profile=HardwareProfile.load(root/'profile.pending.json').validate(arm=False)
    verify_runtime_binding(root,profile)
    calibration=PrototypeCalibration.load(root/'prototype_calibration.json')
    model=PrototypeModel(HERE/'models/lib_two_ridge.json')
    frequencies=[80.,17.8,2.16]
    for f in frequencies:calibration.ratio(profile.binding(Settings().for_frequency(f)))
    if args.verify_only:
        print(json.dumps({'pages':3,'frequencies_hz':frequencies,'calibration_id':calibration.document['id']}));return
    from pynq import Overlay
    overlay=Overlay(str(root/'runtime/battery_eis.bit'),download=not args.reuse_overlay)
    hardware=FastHardware(overlay);hardware.original_profile=profile
    output=root/'data'
    try:
        if args.once:
            job=PrototypeJob(hardware,profile,Settings(),frequencies,output,calibration,model=model,budget_s=20.)
            job.run()
            state=job.snapshot()
            print('ONCE_STATE',json.dumps(state,ensure_ascii=False),flush=True)
            (root/'prototype_last_state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
            return 0 if state['status']=='DONE' else 2
        import app
        app.MeasurementJob=PrototypeJob
        ui_args=SimpleNamespace(sweep=frequencies,position='normal',pair_id=None,confirm_zero_before_move=False,
            output=output,budget=20.,prototype_mode=True,live_controls=True)
        print('UI_READY pages=3',calibration.document['id'],flush=True)
        app.panel_loop(hardware,overlay,ui_args,profile,Settings(),calibration,
            {'temperature_c':None,'soc_fraction':None},model)
    finally:
        hardware.stop()


if __name__=='__main__':raise SystemExit(main())
