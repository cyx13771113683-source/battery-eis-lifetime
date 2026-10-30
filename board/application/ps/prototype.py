"""Measured-spectrum prototype path with persistent ratio factors and SOH output.

Original production validation and public-data model APIs are unchanged.
"""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import threading
import time
import uuid
import numpy as np
from configuration import Settings, RAW_CAPACITY, CLOCK_HZ, phase_increment
from hardware import BatteryHardware, Capture, RUN, DRIVE, MOMENTS, signed64
from analysis import analyze, moments_from_raw, repeatability
from current_zero import evaluate_zero_drive, preflight_settings
from session import Recorder, json_write, normalize_metadata
from research_inference import numerical_predict

PROTO_SCHEMA = 'battery-eis-prototype/1'


def safety_profile(profile):
    profile.validate(arm=False)
    if (profile.r_load_nominal_ohm, profile.r_shunt_ohm) != (10., .1):
        raise ValueError('requires 10ohm/R100 assembly')
    if profile.maximum_voltage_v < 3.7 or profile.maximum_current_a < .4:
        raise ValueError('prototype cannot relax configured limits')
    return replace(profile, maximum_voltage_v=3.7, maximum_current_a=.4).validate(arm=False)


def check_zero(row):
    if set(row.get('reasons', [])) - {'absolute_zero_current_over_1mA'}:
        raise ValueError('zero signal check failed')
    if row.get('counts') != [305, 305]:
        raise ValueError('zero capture incomplete')
    for key, lo, hi in [('voltage_v',3.,3.7), ('raw_abs_max_a',0.,.01),
                       ('noise_std_a',0.,.0005), ('residual_peak_a',0.,.001),
                       ('half_window_drift_a',-.0002,.0002)]:
        value = row.get(key)
        if type(value) not in (int,float) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError('zero check: '+key)


class PrototypeCalibration:
    def __init__(self, document, path=None):
        self.document = deepcopy(document); self.path = Path(path) if path else None
        d = self.document
        if d.get('schema') != 'battery-prototype-calibration/1' or d.get('mode') != 'prototype':
            raise ValueError('prototype calibration schema')
        if d.get('enabled') is not True or d.get('valid_for_board') is not False or not d.get('id'):
            raise ValueError('prototype calibration state')
        rows = d.get('rows')
        if not isinstance(rows,list) or not rows: raise ValueError('calibration rows required')
        seen = set()
        for row in rows:
            key = json.dumps(row['binding'],sort_keys=True)
            if key in seen: raise ValueError('duplicate calibration binding')
            seen.add(key)
            pair = row['correction']
            if not isinstance(pair,list) or len(pair)!=2 or any(type(x) not in (int,float) or not math.isfinite(x) for x in pair):
                raise ValueError('finite complex correction required')
            if not .5 <= abs(complex(*pair)) <= 2: raise ValueError('correction range')
        json.dumps(d,allow_nan=False)

    @classmethod
    def load(cls,path):
        return cls(json.loads(Path(path).read_text(encoding='utf-8-sig')),path)

    def fresh(self):
        return self.load(self.path) if self.path else self

    def ratio(self,binding):
        matches = [row for row in self.document['rows'] if row['binding']==binding]
        if len(matches)!=1: raise ValueError('calibration configuration mismatch')
        return complex(*matches[0]['correction'])

    def apply(self,result,binding):
        k = self.ratio(binding); value = deepcopy(result)
        raw = complex(*result['impedance_ohm']); corrected = raw*k
        value.update(raw_impedance_ohm=[raw.real,raw.imag], impedance_ohm=[corrected.real,corrected.imag],
            magnitude_ohm=abs(corrected), phase_deg=math.degrees(np.angle(corrected)),
            random_uncertainty_ohm=result['random_uncertainty_ohm']*abs(k),
            prototype_calibration_id=self.document['id'], prototype_mode=True,
            applied_ratio_correction=[k.real,k.imag], calibration_id=None, valid=False)
        return value


class PrototypeModel:
    def __init__(self,path):
        self.path = Path(path); data = self.path.read_bytes()
        self.sha256 = hashlib.sha256(data).hexdigest(); self.model = json.loads(data)
        if self.model.get('schema')!='battery-eis-research-model/1' or self.model.get('valid_for_board') is not False:
            raise ValueError('model schema')

    def reject(self,reason):
        return dict(status='no_result',reason=reason,soh=None,capacity_Ah=None,rul=None,valid_for_board=False)

    def evaluate(self,results,context=None):
        sources={r.get('source') for r in results}
        if len(sources)!=1 or not sources.issubset({'hardware','simulation'}):return self.reject('source')
        values=[]; frequencies=[]
        for f in self.model['frequencies_hz']:
            rows=[r for r in results if abs(r['frequency_hz']-f)<=max(.002,f*.0001)]
            if len(rows)<2 or not all(r.get('quality_ok') and r.get('prototype_mode') for r in rows):
                return self.reject('spectrum_incomplete')
            if len({r['repeat'] for r in rows}) != len(rows): return self.reject('duplicate_repeat')
            z=sum(complex(*r['impedance_ohm']) for r in rows)/len(rows)
            values.append(z); frequencies.append(rows[0]['frequency_hz'])
        features=[z.real for z in values]+[z.imag for z in values]
        if any(not math.isfinite(v) or v<a or v>b for v,a,b in zip(features,self.model['lower'],self.model['upper'])):
            return self.reject('feature_range')
        y=numerical_predict(self.model,features)
        if not math.isfinite(y) or not self.model['target_range'][0]<=y<=min(1.,self.model['target_range'][1]):
            return self.reject('output_range')
        return dict(status='prototype_result',soh=y,capacity_Ah=None,rul=None,valid_for_board=False,
            source_domain='battery_eis_rev_a' if sources=={'hardware'} else 'simulation',model_domain=self.model['source_domain'],
            model_sha256=self.sha256,model_file=self.path.name,features=features,frequency_hz=frequencies,
            soc_fraction=(context or {}).get('soc_fraction'),temperature_c=(context or {}).get('temperature_c'))


class FastHardware(BatteryHardware):
    def read_capture(self,deadline=None):
        status=self.read_status()
        if status&2 or not status&4 or not status&16: raise RuntimeError('capture not frozen')
        sequence=(status>>8)&255
        if sequence!=self.expected_sequence: raise RuntimeError('capture sequence changed')
        count,ticks,faults,seq=self.page(9)
        if not 0<=count<=RAW_CAPACITY or seq!=sequence: raise RuntimeError('capture metadata')
        adc=self.page(10);cfg=self.page(11);moments=[[],[]]
        for j in range(9):
            a,b,c,d=self.page(j)
            moments[0].append(signed64(a,b));moments[1].append(signed64(c,d))
        raw=np.empty((count,4),dtype=np.int64)
        self.control &= ~(MOMENTS|(RAW_CAPACITY-1))
        control=self.control;config=self.config.array;left=self.left.array;right=self.right.array
        for j in range(count):
            if j%128==0 and deadline is not None and time.monotonic()>=deadline:
                raise TimeoutError('readback deadline')
            config[2]=control|j
            t=int(left[0]);p=int(left[2]);code=int(right[0]);ch=int(right[2])
            raw[j]=[t,p,code-(1<<32) if code&(1<<31) else code,ch]
        if self.read_status()!=status: raise RuntimeError('capture changed during readback')
        return Capture(raw,tuple(moments),dict(sequence=sequence,status=status,faults=faults,ticks=ticks,
            adc_registers=adc,configuration=cfg,source='hardware',timestamp_convention='conversion-ready DRDY'))


class PrototypeRecorder(Recorder):
    def __init__(self,parent,profile,settings,metadata,source):
        context=normalize_metadata(dict(metadata or {},prototype_mode=True))
        self.path=Path(parent)/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
        self.path.mkdir(parents=True,exist_ok=False);self.index=[];self.source=source
        self.metadata=dict(schema=PROTO_SCHEMA,source=source,utc_started=datetime.now(timezone.utc).isoformat(),
            profile=asdict(profile),settings=asdict(settings),context=context,prototype_mode=True)
        json_write(self.path/'session.json',self.metadata)

    def finish(self,status,results,error=None,ai=None,timing=None):
        summary=dict(schema=PROTO_SCHEMA,status=status,source=self.source,results=results,captures=self.index,
            error=error,ai=ai,timing=timing,prototype_mode=True,utc_finished=datetime.now(timezone.utc).isoformat())
        json_write(self.path/'summary.staged.json',summary)
        return summary


class PrototypeJob:
    def __init__(self,hardware,profile,settings,frequencies,output,calibration,metadata=None,
                 source='hardware',position='normal',model=None,budget_s=20.,current_zero=None):
        if source not in ('hardware','simulation') or position!='normal': raise ValueError('normal battery connection required')
        self.hardware=hardware;self.original_profile=getattr(hardware,'original_profile',profile)
        self.profile=safety_profile(self.original_profile);self.settings=settings.validate()
        self.plan=[settings.for_frequency(f) for f in frequencies]
        if not self.plan or len({s.actual_frequency_hz for s in self.plan})!=len(self.plan): raise ValueError('frequency plan')
        self.calibration=calibration.fresh()
        for s in self.plan:self.calibration.ratio(self.profile.binding(s))
        self.budget_s=float(budget_s)
        if not .4+sum((s.capture_s+s.settle_s)*s.repeats for s in self.plan)<self.budget_s<=60:
            raise ValueError('measurement window budget')
        self.model=model;self.results=[];self.thread=None;self.lock=threading.Lock();self.cancel_event=threading.Event()
        self._finalizing=False
        self.recorder=PrototypeRecorder(output,self.profile,settings,metadata,source)
        json_write(self.recorder.path/'prototype_calibration_used.json',self.calibration.document)
        self.state=dict(status='IDLE',progress=0.,frequency_hz=self.plan[0].frequency_hz,result=None,error=None,
            completed=0,total=sum(s.repeats for s in self.plan),output=str(self.recorder.path),source=source,
            prototype_mode=True,ai=model.reject('awaiting_measurement'))
        self.timing=dict(budget_s=self.budget_s,stages_s={})
        lutpath=Path(__file__).resolve().parents[1]/'rtl/sine14.mem'
        self.lut=np.array([int(x,16) for x in lutpath.read_text().split()],dtype=np.int64)
        self.lut=np.where(self.lut>=8192,self.lut-16384,self.lut)

    def snapshot(self):
        with self.lock:return dict(self.state)

    def update(self,**fields):
        with self.lock:self.state.update(fields)

    def start(self):
        with self.lock:
            if self.thread is not None:raise RuntimeError('job already started')
            self.state['status']='CANCELLING' if self.cancel_event.is_set() else 'INITIALIZING'
            self.thread=threading.Thread(target=self.run,daemon=True,name='battery-prototype')
        self.thread.start()

    def cancel(self):
        with self.lock:
            if self._finalizing or self.state['status'] in ('DONE','ERROR','CANCELLED','TIMEOUT'):return
            self.cancel_event.set();self.state['status']='CANCELLING'

    def check(self):
        if self.cancel_event.is_set():raise InterruptedError('cancelled')
        if time.monotonic()>=self.deadline:raise TimeoutError('measurement deadline')

    def capture(self,settings,drive):
        self.check();h=self.hardware;h.stop()
        for op,value in [(1,phase_increment(settings.frequency_hz)),(2,round(settings.settle_s*CLOCK_HZ)),
            (3,round(settings.capture_s*CLOCK_HZ)),*list(zip((4,5,6),self.profile.trip_codes())),(7,settings.gate_high_code)]:
            h.command(op,value)
        baseline=(h.read_status()>>8)&255;h.control|=RUN
        if drive:h.control|=DRIVE
        else:h.control&=~DRIVE
        h._write();h.heartbeat(force=True)
        h.expected_sequence=(h.read_status()>>8)&255
        if h.expected_sequence==baseline:raise RuntimeError('stale capture')
        until=min(self.deadline,time.monotonic()+settings.capture_s+settings.settle_s+2.)
        while time.monotonic()<until:
            self.check();status=h.poll()
            if status&4 and status&16 and not status&2:
                c=h.read_capture(deadline=self.deadline)
                if not np.array_equal(moments_from_raw(c.raw,self.lut),c.moments):raise RuntimeError('raw/PL moments')
                return c
            time.sleep(.003)
        raise TimeoutError('capture deadline')

    def run(self):
        started=time.monotonic();self.deadline=started+self.budget_s
        terminal='ERROR';error=None;ai=self.model.reject('session_incomplete')
        try:
            self.check()
            self.update(status='INITIALIZING')
            init=self.hardware.initialize();json_write(self.recorder.path/'adc_selfcal.json',init)
            self.update(status='PREFLIGHT');pre=preflight_settings(self.settings)
            c=self.capture(pre,False);stem=self.recorder.capture(c,pre,self.profile,'preflight')
            zero=evaluate_zero_drive(c,self.profile);self.recorder.result(stem,zero);check_zero(zero)
            for s in self.plan:
                for repeat in range(s.repeats):
                    self.update(status='CAPTURING',frequency_hz=s.frequency_hz)
                    c=self.capture(s,True);stem=self.recorder.capture(c,s,self.profile,'normal')
                    if c.meta.get('faults'):raise RuntimeError('PL protection stop')
                    self.update(status='ANALYZING');raw=analyze(c,s,self.profile,None)
                    if not raw['quality_ok'] or abs(complex(*raw['current_ac_a']))<.05:
                        self.recorder.result(stem,raw);raise RuntimeError('signal quality')
                    result=self.calibration.apply(raw,self.profile.binding(s))
                    result.update(repeat=repeat,source=self.recorder.source,position='normal',
                        cell_id=self.profile.cell_id,binding=self.profile.binding(s),raw_file=stem+'_raw.csv')
                    self.recorder.result(stem,result);self.results.append(result)
                    n=len(self.results);self.update(result=result,completed=n,progress=n/self.state['total'])
                    print('POINT',s.frequency_hz,repeat,result['magnitude_ohm']*1000,flush=True)
            for s in self.plan:
                spread=repeatability([r for r in self.results if r['binding']==self.profile.binding(s)])
                if spread['relative_std'] is None or spread['relative_std']>.05:raise RuntimeError('repeatability')
            self.check()
            ai=(self.model.reject('single_point_only') if len(self.plan)==1 else
                self.model.evaluate(self.results,self.recorder.metadata['context']))
            self.check()
            terminal='DONE'
        except InterruptedError as exc:terminal='CANCELLED';error=str(exc)
        except TimeoutError as exc:terminal='TIMEOUT';error=str(exc)
        except Exception as exc:terminal='ERROR';error=type(exc).__name__+': '+str(exc)
        finally:
            try:
                self.hardware.stop()
                for op,value in zip((4,5,6),self.original_profile.trip_codes()):self.hardware.command(op,value)
            except Exception as exc:terminal='ERROR';error='stop: '+str(exc)
            with self.lock:
                self._finalizing=True
                if terminal=='DONE' and self.cancel_event.is_set():terminal='CANCELLED';error='cancelled'
            if terminal=='DONE' and time.monotonic()>=self.deadline:terminal='TIMEOUT';error='measurement deadline'
            if terminal!='DONE':ai=self.model.reject('session_incomplete')
            self.timing['elapsed_before_summary_s']=time.monotonic()-started
            try:
                prepared=self.recorder.finish(terminal,self.results,error,ai,self.timing)
                if terminal=='DONE' and time.monotonic()>=self.deadline:
                    terminal='TIMEOUT';error='save deadline';ai=self.model.reject('session_incomplete')
                prepared.update(status=terminal,error=error,ai=ai)
                self.recorder.commit_summary(prepared)
                if terminal=='DONE' and time.monotonic()>=self.deadline:
                    terminal='TIMEOUT';error='commit deadline';ai=self.model.reject('session_incomplete')
                    self.recorder.revise_failed_terminal(terminal,self.results,error,ai,self.timing)
            except Exception as exc:terminal='ERROR';error='save: '+str(exc);ai=self.model.reject('session_incomplete')
            self.update(status=terminal,error=error,ai=ai,elapsed_s=time.monotonic()-started)
            print('RESULT',terminal,ai.get('soh'),self.state['elapsed_s'],flush=True)
