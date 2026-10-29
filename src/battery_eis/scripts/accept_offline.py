"""One-command host acceptance; never imports PYNQ or enables a physical load."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import zipfile

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'release/be03_ps_20261022_b'

def load(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,value):
    with p.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)

# Only explicit host entry points are called. This guard additionally fails a
# process if an offline path accidentally tries to import a board I/O package.
GUARD="""import importlib.abc,runpy,sys
class NoHardware(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname.split('.')[0] in ('pynq','spidev','gpiod','RPi'):
   raise RuntimeError('Offline acceptance blocked physical I/O import: '+fullname)
sys.meta_path.insert(0,NoHardware())
script=sys.argv[1];sys.argv=sys.argv[1:]
sys.path.insert(0,__import__('os').path.dirname(script))
runpy.run_path(script,run_name='__main__')
"""

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    env=os.environ.copy();env.update(PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
    steps=[]
    def run(name,arguments,expected=0,script=None):
        cmd=([sys.executable,'-B','-c',GUARD,str(script)]+arguments) if script else [sys.executable,'-B']+arguments
        began=time.monotonic()
        result=subprocess.run(cmd,cwd=ROOT,env=env,capture_output=True,text=True,timeout=120)
        (out/(name+'.log')).write_text(result.stdout+result.stderr,encoding='utf-8')
        steps.append(dict(name=name,exit_code=result.returncode,expected_exit=expected,elapsed_s=time.monotonic()-began))
        if result.returncode!=expected:raise RuntimeError(name+' failed; see saved log')
        return result
    def cli(name,arguments,expected=0):return run(name,arguments,expected,ROOT/'ps/app.py')
    try:
        baseline=load(ROOT/'assets/offline_runtime_baseline_be03_b.json')
        for name,h in baseline['files'].items():
            if sha(ROOT/name)!=h:raise ValueError('Frozen runtime changed: '+name)
        archive=BASE/'be03_ps_20261022_b.zip'
        # A standalone later release carries baseline hashes, not a nested old
        # ZIP. When the original B archive is present, also verify that copy.
        if archive.exists():
            if sha(archive)!=baseline['archive_sha256']:raise ValueError('Frozen B archive changed')
            with zipfile.ZipFile(archive) as z:
                if z.testzip() is not None:raise ValueError('Frozen B archive CRC failed')
        test=run('host_tests_final',['-m','unittest','discover','-s','tests','-v'])
        count=re.search(r'Ran (\d+) tests',test.stderr)
        if not count or not re.search(r'^OK\s*$',test.stderr,re.M):raise ValueError('Test result absent')
        run('synthetic_workflow',[str(out/'workflow')],script=ROOT/'scripts/validate_prepcb.py')
        validation=load(out/'workflow/validation.json');session=Path(validation['simulation_session'])
        cli('audit',['audit-session','--session',str(session),'--output',str(out/'audit.json')])
        cli('candidate',['export-candidate','--session',str(session),'--output',str(out/'candidate.json')])
        run('diagnostic',['--session',str(session),'--output',str(out/'diagnostic.json')],script=ROOT/'scripts/diagnose_session.py')
        candidate=load(out/'candidate.json');diagnostic=load(out/'diagnostic.json')
        if candidate['source']!='simulation' or candidate['training_eligible'] or any(v is not None for v in candidate['labels'].values()):
            raise ValueError('Synthetic data/label separation failed')
        if diagnostic['diagnostic_valid_for_measurement'] or diagnostic['model_evaluation_allowed']:
            raise ValueError('Research diagnostic gate failed')
        # Independent corruption of raw bytes must fail the public CLI, not just
        # an in-process helper. Keep the rejected input for inspection.
        corrupt=out/'corrupt_session';shutil.copytree(session,corrupt)
        raw=corrupt/'001_raw.csv'
        rows=list(csv.reader(raw.read_text().splitlines()));rows[2][3]=str(int(rows[2][3])+1)
        with raw.open('w',newline='',encoding='utf-8') as f:csv.writer(f).writerows(rows)
        failure=cli('corruption_rejected',['audit-session','--session',str(corrupt),'--output',str(out/'must_not_exist.json')],1)
        if 'PL moments' not in failure.stderr or (out/'must_not_exist.json').exists():raise ValueError('Corruption negative control failed')
        kit=out/'arrival_kit';kit.mkdir()
        shutil.copy2(ROOT/'config/profile.example.json',kit/'profile.pending.json')
        write(kit/'metadata.pending.json',dict(temperature_c=None,soc_fraction=None,aging_group=None,port_kind='battery_terminal'))
        cli('template',['calibration-template','--profile',str(kit/'profile.pending.json'),'--sweep','2.16','17.8','--output',str(kit/'reference.pending.json')])
        pending=load(kit/'reference.pending.json')
        if pending['evidence_kind']!='pending_reference':raise ValueError('Reference template not pending')
        # Placeholder templates must not become a real transfer calibration.
        rejected=cli('empty_reference_rejected',['calibrate-transfer','--input',str(kit/'reference.pending.json'),'--output',str(kit/'must_not_exist_transfer.json')],1)
        if (kit/'must_not_exist_transfer.json').exists():raise ValueError('Empty reference was promoted')
        write(kit/'capacity_pairing.pending.json',dict(schema='battery-capacity-pairing-pending/1',cell_id=None,
            aging_group=None,eis_candidate_id=None,capacity_test_utc=None,instrument_id=None,protocol=None,
            raw_capacity_file=None,raw_capacity_sha256=None,Q_ref_Ah=None,Q0_ref_Ah=None,
            target_definition=None,reviewer=None,training_eligible=False,
            note='Fill independent capacity evidence; no AI output is accepted as a label. Split by cell and aging group.'))
        write(kit/'acceptance.pending.json',dict(schema='battery-physical-acceptance-pending/1',status='pending',
            checks={k:None for k in ('assembly','pinout','power_levels','adc_clock','zero_current','protection',
                'dc_reference','complex_reference','ab_standard','transient_and_linearity','noise_floor','arm_full_time','target_model_validation')},
            evidence_files=[],approved_by=None,approved_utc=None))
        report=dict(schema='battery-offline-acceptance/1',status='PASS',host_tests=int(count[1]),steps=steps,
            frozen_runtime_b_unchanged=True,frozen_b_archive_present=archive.exists(),guarded_host_workflow=True,raw_reconstruction=True,
            unlabeled_export=True,research_diagnostic_separated=True,corruption_negative_control=True,
            empty_reference_negative_control=True,window_budget_s=validation['window_budget_s'],
            board_validated=False,full_20s_hardware_validated=False,target_battery_model_validated=False,
            physical_checks_pending=list(load(kit/'acceptance.pending.json')['checks']),
            note='Host software acceptance only. Virtual/synthetic elapsed time is not hardware performance.')
        write(out/'acceptance.json',report)
        write(out/'evidence_sha256.json',{p.relative_to(out).as_posix():sha(p) for p in out.rglob('*') if p.is_file()})
        print(json.dumps(report,ensure_ascii=False,indent=2))
    except Exception as exc:
        write(out/'failure.json',dict(status='FAIL',error=str(exc),steps=steps))
        raise

if __name__=='__main__':main()
