"""One worker at a time: continuous single-point and interrupting SOH sweeps."""
from copy import deepcopy
import json,math,os
from pathlib import Path
import uuid
from analysis import repeatability

TERMINAL={'DONE','UNVERIFIED','ERROR','TIMEOUT','CANCELLED'}


class RetainedSpectrum:
    """Display-only last completed value at each frequency; never model input."""
    def __init__(self,root,frequencies):
        self.root=Path(root);self.path=self.root/'display_spectrum.latest.json'
        self.frequencies=tuple(frequencies);self.points={};self.seen={};self.warning=None
        try:
            saved=json.loads(self.path.read_text(encoding='utf-8'))
            for row in saved['points']:self._keep(row)
        except (OSError,ValueError,TypeError,KeyError):pass
        if not self.points and self.root.is_dir():
            folders=sorted((p for p in self.root.iterdir() if p.is_dir()),key=lambda p:p.stat().st_mtime_ns)
            for folder in folders[-40:]:
                try:
                    s=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
                    if s.get('status')=='DONE':self.ingest(s.get('results',[]),str(folder),save=False)
                except (OSError,ValueError,TypeError,KeyError):pass

    def _keep(self,row):
        f=row.get('requested_frequency_hz');z=row.get('impedance_ohm')
        if f not in self.frequencies or not isinstance(z,list) or len(z)!=2:return False
        if any(not isinstance(x,(int,float)) or not math.isfinite(x) for x in z):return False
        if row.get('quality_ok') is not True or not row.get('prototype_calibration_id'):return False
        self.points[f]=deepcopy(row);return True

    def ingest(self,results,session,save=True):
        changed=False
        for f in self.frequencies:
            rows=[r for r in results if r.get('requested_frequency_hz')==f and r.get('quality_ok')]
            signature=(session,len(rows))
            if len(rows)<2 or self.seen.get(f)==signature:continue
            if len({r.get('repeat') for r in rows})!=len(rows):continue
            spread=repeatability(rows)
            if spread['relative_std'] is None or spread['relative_std']>.05:continue
            z=sum(complex(*r['impedance_ohm']) for r in rows)/len(rows)
            value=dict(rows[-1],impedance_ohm=[z.real,z.imag],magnitude_ohm=abs(z),
                       phase_deg=math.degrees(math.atan2(z.imag,z.real)),display_only=True,
                       display_session=session,display_repeats=len(rows))
            if self._keep(value):self.seen[f]=signature;changed=True
        if changed and save:
            temporary=self.path.with_name('.display-'+uuid.uuid4().hex+'.tmp')
            try:
                self.root.mkdir(parents=True,exist_ok=True)
                temporary.write_text(json.dumps(dict(schema='battery-display-cache/1',points=self.rows()),allow_nan=False),encoding='utf-8')
                os.replace(temporary,self.path);self.warning=None
            except OSError as exc:
                self.warning=str(exc)
                try:temporary.unlink(missing_ok=True)
                except OSError:pass
        return changed

    def rows(self):return [deepcopy(self.points[f]) for f in sorted(self.points)]


class LiveController:
    def __init__(self,factory,model,root,frequencies,selected):
        self.factory=factory;self.model=model;self.frequencies=tuple(frequencies);self.selected=selected
        self.cache=RetainedSpectrum(root,self.frequencies);self.job=None;self.kind=None
        self.single_running=False;self.life_pending=False;self.resume_single=False
        self.life_ai=model.reject('awaiting_measurement');self.last_life=None;self.revision=0
        self.state=dict(status='IDLE',result=None,source='hardware',ai=self.life_ai)

    @staticmethod
    def alive(job):
        thread=getattr(job,'thread',None)
        return bool(thread is not None and thread.is_alive())

    def select(self,frequency):
        if frequency not in self.frequencies or self.life_pending:return
        self.selected=frequency
        if self.job and self.kind=='single' and self.single_running:self.job.cancel()

    def toggle_single(self):
        if self.life_pending:return
        self.single_running=not self.single_running
        if not self.single_running and self.job and self.kind=='single':self.job.cancel()

    def request_lifetime(self):
        if self.life_pending:return
        self.resume_single=self.single_running;self.life_pending=True
        self.life_ai=self.model.reject('awaiting_measurement')
        self.last_life=None
        if self.job and self.kind=='single':self.job.cancel()

    def cancel(self):
        self.single_running=False;self.resume_single=False;self.life_pending=False
        if self.job:self.job.cancel()

    def _launch(self,kind):
        try:
            self.job=self.factory(self.frequencies if kind=='lifetime' else (self.selected,))
            self.kind=kind;self.job.start()
        except (ValueError,RuntimeError,OSError) as exc:
            self.job=None;self.kind=None;self.single_running=False;self.life_pending=False;self.resume_single=False
            self.state=dict(status='ERROR',result=None,error=str(exc),source='hardware',ai=self.model.reject('start_failed'))
            self.life_ai=self.state['ai']

    def tick(self):
        terminal=None;completed_kind=None
        if self.job:
            self.state=self.job.snapshot()
            self.cache.ingest(self.job.results,self.state.get('output',''))
            if self.state['status'] in TERMINAL and not self.alive(self.job):
                terminal=self.state['status'];completed_kind=self.kind
                if self.kind=='lifetime':
                    self.last_life=deepcopy(self.state)
                    self.life_pending=False
                    if terminal=='DONE':
                        self.life_ai=self.state.get('ai',self.model.reject('session_incomplete'))
                        self.single_running=self.resume_single
                    else:self.single_running=False;self.life_ai=self.model.reject('session_incomplete')
                    self.resume_single=False
                elif terminal in ('ERROR','TIMEOUT','UNVERIFIED'):
                    self.single_running=False
                    if not self.life_pending:self.life_ai=self.model.reject('session_incomplete')
                self.revision+=1;self.job=None;self.kind=None
        if self.job is None:
            if self.life_pending:self._launch('lifetime')
            elif self.single_running:self._launch('single')
        if self.job:self.state=self.job.snapshot()
        return terminal,completed_kind

    def snapshot(self):
        state=dict(self.state)
        if not self.life_pending:state['ai']=self.life_ai
        state.update(spectrum=self.cache.rows(),single_running=self.single_running,
                     lifetime_running=self.life_pending,single_paused=self.life_pending and self.resume_single,
                     selected_frequency_hz=self.selected,live_controls=True)
        return state
