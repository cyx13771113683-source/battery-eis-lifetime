"""Finite, cancellable capture workflow. All hardware access stays on one worker."""
from dataclasses import asdict,replace
from datetime import datetime,timezone
from pathlib import Path
import csv
import json
import threading
import time
import uuid
import numpy as np
from configuration import SCHEMA,Settings,digest,finite
from analysis import analyze,repeatability
from calibration_plan import calibration_coverage
from model_bridge import no_model
from current_zero import evaluate_zero_drive,preflight_settings

def json_write(path,value):
    with Path(path).open('x',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)

def normalize_metadata(metadata):
    context=dict(temperature_c=None,soc_fraction=None,cycle_count=None,capacity_ah=None,
                 metadata_source='manual_or_missing',port_kind='battery_terminal')
    context.update(metadata or {})
    for key in ('temperature_c','soc_fraction','cycle_count','capacity_ah'):
        value=context[key]
        if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not np.isfinite(value)):
            raise ValueError('metadata %s must be a finite number or null'%key)
    if context['soc_fraction'] is not None and not 0<=context['soc_fraction']<=1:
        raise ValueError('soc_fraction must be 0..1')
    # Check extension fields too, before creating a partial session or loading PL.
    json.dumps(context,allow_nan=False)
    return context

def validate_request(profile,settings,frequencies,calibration=None,source='hardware',
                     position='normal',budget_s=20.,metadata=None,current_zero=None):
    """Pure validation; safe to call before loading an overlay or opening output."""
    profile.validate(arm=True);settings.validate()
    frequencies=tuple(frequencies)
    if not frequencies or len(frequencies)>20:raise ValueError('choose 1..20 sweep frequencies')
    plan=tuple(settings.for_frequency(frequency) for frequency in frequencies)
    if len({s.actual_frequency_hz for s in plan})!=len(plan):
        raise ValueError('duplicate or DDS-equivalent frequency')
    budget_s=finite(budget_s,.01,120.,'budget_s')
    window_budget_s=.4+sum((s.capture_s+s.settle_s)*s.repeats for s in plan)
    if window_budget_s>=budget_s:raise ValueError('capture windows exceed session time budget')
    if source not in ('hardware','simulation'):raise ValueError('unknown measurement source')
    if current_zero is not None:current_zero.validate_for(profile,source,settings=settings)
    if position not in ('normal','A','B'):raise ValueError('unknown calibration position')
    normalize_metadata(metadata)
    if calibration is not None:
        if source=='hardware' and calibration.document.get('simulation_only') is True:
            raise ValueError('simulation-only calibration cannot be used for hardware')
        coverage=calibration_coverage(calibration,profile,settings,frequencies)
        missing=[str(row['requested_frequency_hz']) for row in coverage['rows'] if row['status']!='matched']
        if missing:raise ValueError('calibration has no exact binding for %s Hz'%(', '.join(missing)))
    return plan,budget_s,window_budget_s

class Recorder:
    def __init__(self,parent,profile,settings,metadata,source):
        context=normalize_metadata(metadata)
        name=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
        self.path=Path(parent)/name
        self.path.mkdir(parents=True,exist_ok=False)
        self.index=[]
        self.source=source
        # Missing context stays null. Manual information is explicitly tagged.
        self.metadata=dict(schema=SCHEMA,source=source,utc_started=datetime.now(timezone.utc).isoformat(),
                           profile=asdict(profile),settings=asdict(settings),context=context,
                           phasor_convention='peak, cos-reference, discharge positive, Z=-V_ac/I_ac')
        json_write(self.path/'session.json',self.metadata)

    def capture(self,capture,settings,profile,position):
        stem='%03d'%len(self.index)
        raw_path=self.path/(stem+'_raw.csv')
        with raw_path.open('x',encoding='utf-8',newline='') as f:
            writer=csv.writer(f);writer.writerow(['sample_index','drdy_tick_100MHz','phase_u32','adc_signed24','channel'])
            for index,row in enumerate(capture.raw):writer.writerow([index,*map(int,row)])
        meta=dict(capture.meta,binding=profile.binding(settings),moments=capture.moments,
                  position=position,source=self.source,utc_saved=datetime.now(timezone.utc).isoformat(),
                  raw_file=raw_path.name,hardware_signature=profile.hardware_id)
        json_write(self.path/(stem+'_capture.json'),meta)
        self.index.append(stem)
        return stem

    def result(self,stem,result):json_write(self.path/(stem+'_result.json'),result)

    def finish(self,status,results,error=None,ai=None,timing=None):
        summary=dict(schema=SCHEMA,status=status,error=error,source=self.source,results=results,
                     captures=self.index,utc_finished=datetime.now(timezone.utc).isoformat(),
                     ai=ai if ai is not None else no_model(),timing=timing)
        json_write(self.path/'summary.staged.json',summary)
        return summary


    def commit_summary(self,summary):
        # The canonical file is absent until a complete staged document has
        # passed the caller's deadline check and atomic publication succeeds.
        staged=self.path/'summary.commit.json'
        json_write(staged,summary)
        staged.rename(self.path/'summary.json')
        return summary

    def revise_failed_terminal(self,status,results,error,ai,timing):
        # Only demote a completed summary. Preserve the provisional evidence
        # before any further write, so a failed revision cannot leave that
        # success file at the canonical name when the rename succeeded.
        if status not in ('TIMEOUT','ERROR'):
            raise ValueError('terminal revision can only record failure')
        old=self.path/'summary.json'
        index=0
        backup=self.path/'summary.before_overrun.json'
        while backup.exists():
            index+=1;backup=self.path/('summary.before_overrun_%d.json'%index)
        old.rename(backup)
        summary=json.loads(backup.read_text(encoding='utf-8'))
        summary.update(status=status,error=error,ai=ai,timing=timing,
                       utc_finished=datetime.now(timezone.utc).isoformat())
        return self.commit_summary(summary)

class MeasurementJob:
    def __init__(self,hardware,profile,settings,frequencies,output,calibration=None,metadata=None,
                 source='hardware',position='normal',model=None,budget_s=20.,current_zero=None):
        self.hardware=hardware;self.profile=profile.validate(arm=True);self.settings=settings.validate()
        self.frequencies=tuple(frequencies)
        self.plan,self.budget_s,self.window_budget_s=validate_request(profile,settings,
            self.frequencies,calibration,source,position,budget_s,metadata,current_zero)
        self.current_zero=current_zero;self.current_offset_a=0.
        self.model=model;self.deadline=None
        self.timing=dict(budget_s=self.budget_s,window_budget_s=self.window_budget_s,stages_s={},
                         scope='monotonic job time; display transfer excluded; cooperative deadline')
        self.calibration=calibration;self.metadata=metadata or {};self.source=source;self.position=position
        self.recorder=Recorder(output,profile,settings,self.metadata,source)
        if current_zero is not None:
            json_write(self.recorder.path/'current_zero_used.json',dict(document=current_zero.document,
                document_sha256=digest(current_zero.document),scope='independent zero reference; raw protection unchanged'))
        if calibration is not None:
            json_write(self.recorder.path/'calibration_used.json',dict(
                document=calibration.document,document_sha256=digest(calibration.document),
                scope='coefficients used in this session; physical reference validity is separate'))
        json_write(self.recorder.path/'measurement_plan.json',dict(
            frequency_settings=[asdict(s) for s in self.plan],window_budget_s=self.window_budget_s,budget_s=self.budget_s))
        self.cancel_event=threading.Event();self.thread=None;self.lock=threading.Lock();self._finalizing=False
        self.state=dict(status='IDLE',progress=0.,frequency_hz=self.frequencies[0],result=None,
                        error=None,completed=0,total=len(self.frequencies)*settings.repeats,
                        output=str(self.recorder.path),source=source,
                        ai=no_model() if model is None else model.reject('awaiting_measurement'))
        self.results=[]

    def snapshot(self):
        with self.lock:return dict(self.state)

    def update(self,**kwargs):
        with self.lock:self.state.update(kwargs)

    def start(self):
        if self.thread is not None:raise RuntimeError('job already started')
        self.thread=threading.Thread(target=self.run,name='battery-capture',daemon=True)
        self.thread.start()

    def cancel(self):
        with self.lock:
            if self._finalizing or self.state['status'] in ('DONE','UNVERIFIED','CANCELLED','ERROR','TIMEOUT'):
                return
            self.cancel_event.set();self.state['status']='CANCELLING'

    def _budget_check(self):
        if self.deadline is not None and time.monotonic()>=self.deadline:
            raise TimeoutError('whole-session time budget exceeded')

    def _timed(self,name,call,*args,**kwargs):
        self._budget_check()
        start=time.monotonic()
        try:return call(*args,**kwargs)
        finally:
            stages=self.timing['stages_s']
            stages[name]=stages.get(name,0.)+time.monotonic()-start

    def _read_capture(self):
        return self._timed('readback',self.hardware.read_capture,deadline=self.deadline)

    def _wait_capture(self,settings):
        until=time.monotonic()+settings.settle_s+settings.capture_s+1.
        while time.monotonic()<until:
            self._budget_check()
            if self.cancel_event.is_set():
                self.hardware.stop()
                # Preserve a cancelled partial record when firmware has flushed it.
                for _ in range(20):
                    status=self.hardware.poll()
                    if status&4 and status&16:return self._read_capture()
                    time.sleep(.001)
                raise InterruptedError('cancelled before a capture could be frozen')
            status=self.hardware.poll()
            if status&4 and status&16:return self._read_capture()
            time.sleep(.005)
        self.hardware.stop()
        raise TimeoutError('capture deadline exceeded')

    def run(self):
        terminal='ERROR';error=None;repeatability_failed=False
        started=time.monotonic();self.deadline=started+self.budget_s
        ai=no_model() if self.model is None else self.model.reject('session_incomplete')
        try:
            self.update(status='INITIALIZING')
            init=self._timed('initialize',self.hardware.initialize)
            json_write(self.recorder.path/'adc_selfcal.json',dict(init,scope='ADC internal self-calibration only'))
            if self.cancel_event.is_set():raise InterruptedError('cancelled')
            # DA0=0 off-load voltage and leakage check before any positive gate code.
            self.update(status='PREFLIGHT')
            check=preflight_settings(self.settings)
            self._timed('configure',self.hardware.start,check,self.profile,drive=False)
            capture=self._timed('wait_and_readback',self._wait_capture,check)
            stem=self._timed('save_raw',self.recorder.capture,capture,check,self.profile,'preflight')
            if self.cancel_event.is_set():raise InterruptedError('cancelled')
            zero=evaluate_zero_drive(capture,self.profile,self.current_zero,self.source,settings=self.settings)
            self.recorder.result(stem,zero)
            if not zero['valid']:raise RuntimeError('zero-drive check failed: '+', '.join(zero['reasons']))
            self.current_offset_a=zero['offset_applied_a']
            for settings in self.plan:
                frequency=settings.frequency_hz
                point=[]
                for repeat in range(settings.repeats):
                    if self.cancel_event.is_set():raise InterruptedError('cancelled')
                    self.update(status='CAPTURING',frequency_hz=frequency,result=None)
                    self._timed('configure',self.hardware.start,settings,self.profile,drive=True)
                    capture=self._timed('wait_and_readback',self._wait_capture,settings)
                    stem=self._timed('save_raw',self.recorder.capture,capture,settings,self.profile,self.position)
                    if self.cancel_event.is_set():raise InterruptedError('cancelled')
                    self.update(status='ANALYZING')
                    result=self._timed('analysis',analyze,capture,settings,self.profile,self.calibration)
                    if self.current_zero is not None:
                        result.update(current_dc_raw_a=result['current_dc_a'],current_dc_offset_a=self.current_offset_a,
                            current_dc_corrected_a=result['current_dc_a']-self.current_offset_a,
                            current_zero_reference_sha256=digest(self.current_zero.document))
                    result.update(repeat=repeat,source=self.source,position=self.position,
                                  cell_id=self.profile.cell_id,binding=self.profile.binding(settings),
                                  raw_file=stem+'_raw.csv')
                    self.recorder.result(stem,result);self.results.append(result);point.append(result)
                    completed=len(self.results)
                    self.update(result=result,completed=completed,progress=completed/self.state['total'])
                    if capture.meta.get('faults'):raise RuntimeError('PL stopped capture: %s'%capture.meta['faults'])
                spread=repeatability(point)
                json_write(self.recorder.path/('repeatability_%s.json'%frequency),spread)
                if spread['relative_std'] is not None and spread['relative_std']>.05:
                    repeatability_failed=True
                    for result in point:
                        result['valid']=False
                        result['quality_ok']=False
                        result['reasons'].append('repeatability_exceeds_5pct')
                    error='repeatability exceeds 5%; per-capture files precede this session-level check'
                    self.update(error=error)
            terminal='DONE' if not repeatability_failed and all(r['valid'] for r in self.results) else 'UNVERIFIED'
            if self.cancel_event.is_set():raise InterruptedError('cancelled')
            self._budget_check()
            if self.model is not None:
                ai=self._timed('inference',self.model.evaluate,self.results,self.recorder.metadata['context'],
                               self.profile.chemistry,self.source,terminal,self.position)
                if self.cancel_event.is_set():raise InterruptedError('cancelled')
                self._budget_check()
        except InterruptedError as exc:terminal='CANCELLED';error=str(exc)
        except TimeoutError as exc:terminal='TIMEOUT';error=str(exc)
        except Exception as exc:terminal='ERROR';error='%s: %s'%(type(exc).__name__,exc)
        finally:
            try:self.hardware.stop()
            except Exception as exc:terminal='ERROR';error='stop acknowledgement failed: '+str(exc)
            # Commit one terminal result atomically with cancellation. A later
            # click during disk finalization cannot diverge UI and saved status.
            # Never hold the UI lock over filesystem I/O.
            with self.lock:
                self._finalizing=True
                if terminal in ('DONE','UNVERIFIED') and self.cancel_event.is_set():
                    terminal='CANCELLED';error='cancelled'
            if terminal not in ('DONE','UNVERIFIED') and self.model is not None:
                ai=self.model.reject('session_incomplete')
            self.timing['elapsed_before_summary_s']=time.monotonic()-started
            if terminal in ('DONE','UNVERIFIED') and self.timing['elapsed_before_summary_s']>=self.budget_s:
                terminal='TIMEOUT';error='whole-session time budget exceeded'
                if self.model is not None:ai=self.model.reject('session_incomplete')
            try:
                prepared=self.recorder.finish(terminal,self.results,error,ai,self.timing)
                elapsed_after_summary=time.monotonic()-started
                self.timing['elapsed_after_primary_summary_s']=elapsed_after_summary
                if terminal in ('DONE','UNVERIFIED') and elapsed_after_summary>=self.budget_s:
                    terminal='TIMEOUT';error='whole-session budget exceeded during final summary write'
                    if self.model is not None:ai=self.model.reject('session_incomplete')
                prepared.update(status=terminal,error=error,ai=ai,timing=self.timing)
                self.recorder.commit_summary(prepared)
                elapsed_after_commit=time.monotonic()-started
                if terminal in ('DONE','UNVERIFIED') and elapsed_after_commit>=self.budget_s:
                    terminal='TIMEOUT';error='whole-session budget exceeded during final summary commit'
                    self.timing['elapsed_after_primary_commit_s']=elapsed_after_commit
                    if self.model is not None:ai=self.model.reject('session_incomplete')
                    self.recorder.revise_failed_terminal(terminal,self.results,error,ai,self.timing)
            except Exception as exc:
                terminal='ERROR';error='record finalization failed: %s: %s'%(type(exc).__name__,exc)
                if self.model is not None:ai=self.model.reject('session_incomplete')
            self.update(status=terminal,error=error,ai=ai,elapsed_s=time.monotonic()-started)
