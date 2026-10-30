#!/usr/bin/env python3
"""Battery EIS REV A host/ARM entry point. See ../README.md for physical preflight."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import signal
import time
from copy import deepcopy
from configuration import HardwareProfile,Settings,DEFAULT_SWEEP
from calibration import Calibration,from_reference,ab_check,export_ab_session
from calibration_plan import reference_template,calibration_coverage,CALIBRATION_FREQUENCIES
from session import MeasurementJob,json_write,validate_request
from model_bridge import HealthModel
from current_zero import CurrentZeroReference

BASE=Path(__file__).resolve().parents[1]

def parse():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('measure','panel','simulate'):
        p=sub.add_parser(name)
        p.add_argument('--profile',type=Path,required=True)
        p.add_argument('--overlay',type=Path)
        p.add_argument('--calibration',type=Path)
        p.add_argument('--current-zero',type=Path,help='independently verified zero reference; never auto-unlocks profile')
        p.add_argument('--output',type=Path,default=BASE/'data')
        p.add_argument('--frequency',type=float)
        p.add_argument('--sweep',type=float,nargs='+')
        p.add_argument('--repeats',type=int,default=2)
        p.add_argument('--model',type=Path,default=BASE/'models/frozen_two_frequency_20261022/lib_two_ridge.json')
        p.add_argument('--budget',type=float,default=20.)
        p.add_argument('--metadata',type=Path)
        p.add_argument('--position',choices=('normal','A','B'),default='normal')
        p.add_argument('--pair-id')
        p.add_argument('--confirm-zero-before-move',action='store_true')
    for name in ('calibrate-transfer','ab-check'):
        p=sub.add_parser(name);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    for name in ('calibration-template','calibration-check'):
        p=sub.add_parser(name)
        p.add_argument('--profile',type=Path,required=True)
        p.add_argument('--sweep',type=float,nargs='+',default=CALIBRATION_FREQUENCIES)
        p.add_argument('--output',type=Path,required=True)
        if name=='calibration-check':p.add_argument('--calibration',type=Path,required=True)
    p=sub.add_parser('preview');p.add_argument('--output',type=Path,default=BASE/'reports/ui')
    p=sub.add_parser('replay-model',help='public dataset replay; never a battery measurement')
    p.add_argument('--model',type=Path,required=True);p.add_argument('--input',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('ab-export');p.add_argument('--session',type=Path,required=True)
    p.add_argument('--calibration',type=Path)
    p.add_argument('--frequency',type=float,default=80.);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('audit-session',help='offline raw-data and calibration reconstruction; no hardware access')
    p.add_argument('--session',type=Path,required=True);p.add_argument('--calibration',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('export-candidate',help='audited unlabelled dataset candidate; never automatic capacity labels')
    p.add_argument('--session',type=Path,required=True);p.add_argument('--calibration',type=Path)
    p.add_argument('--output',type=Path,required=True)
    return parser.parse_args()

# Operator UI has exactly three pages since 2026-10-27: 0=首页, 1=设置, 2=记录.
# The 校准 and 状态 pages were deleted by user decision; do not restore them.
def panel_loop(hardware,overlay,args,profile,settings,calibration,metadata,model):
    import numpy as np
    from panel import Painter,action,BUSY
    from ui_records import RecordBrowser,start_notice
    from linux_touch import TouchReader
    from pynq.lib.video import VideoMode
    painter=Painter();hdmi=overlay.video.hdmi_out
    if hdmi._vdma is None:hdmi._vdma=overlay.video.axi_vdma
    hdmi.configure(VideoMode(800,600,24,60));hdmi.start()
    touch=None;job=None;page=0;selected=args.sweep[0];sweep=len(args.sweep)>1;position=args.position
    alive=True;next_scan=0
    browser=RecordBrowser(args.output,current_run_only=bool(getattr(args,'live_controls',False)));history_seen=None
    first_frame=True;ui_started=None;reported_output=None
    live=bool(getattr(args,'live_controls',False));controller=None;last_view=None;revision=-1
    if live:print('UI_RECORDS_RUN_START count=%d'%len(browser.index.entries),flush=True)
    if live:
        from live_control import LiveController
        def create_job(frequencies):
            hardware.stop()
            if position!='normal' and (not args.pair_id or not args.confirm_zero_before_move):
                raise ValueError('A/B requires pair-id and move confirmation')
            return MeasurementJob(hardware,profile,settings,frequencies,args.output,calibration,
                dict(metadata,pair_id=args.pair_id,zero_ack_before_move=args.confirm_zero_before_move),
                position=position,model=model,budget_s=args.budget,
                current_zero=getattr(args,'current_zero_reference',None))
        controller=LiveController(create_job,model,args.output,args.sweep,selected)
    state=dict(status='IDLE',result=None,frequency_hz=selected,source='hardware',ai=model.reject('awaiting_measurement'))
    def finish(_s,_f):
        nonlocal alive
        alive=False
    signal.signal(signal.SIGINT,finish);signal.signal(signal.SIGTERM,finish)
    try:
        while alive:
            if live:
                controller.tick();job=controller.job;state=controller.snapshot()
                if controller.revision!=revision:browser.refresh();revision=controller.revision
            elif job:
                state=job.snapshot()
                state['spectrum']=[dict(row) for row in job.results]
                if state['status'] in ('DONE','UNVERIFIED','ERROR','TIMEOUT','CANCELLED') and history_seen!=state['output']:
                    browser.refresh();history_seen=state['output']
            state['context']=metadata
            state['prototype_mode']=bool(getattr(args,'prototype_mode',False))
            busy=state.get('status') in BUSY or state.get('single_running') or state.get('lifetime_running')
            if touch is None and time.monotonic()>=next_scan:
                next_scan=time.monotonic()+2
                try:touch=TouchReader.autodetect(800,600)
                except OSError:touch=None
            if touch is not None:
                try:
                    point,changed=touch.poll(.01)
                    request=action(point,page,busy,record_detail=browser.selected is not None,
                        viewing_record=browser.chart_record is not None,
                        notice=bool(state.get('ui_notice')),live_controls=live,
                        lifetime_running=state.get('lifetime_running',False)) if changed and touch.just_pressed and point is not None else None
                    if request:
                        key,value=request
                        if key=='dismiss_notice':
                            state.pop('ui_notice',None)
                            if live:controller.state.pop('ui_notice',None)
                        elif key=='page':
                            page=value
                            if page==2:browser.refresh()
                        elif key=='record_page':browser.move(value)
                        elif key=='record_open':browser.open(value)
                        elif key=='record_back':browser.back()
                        elif key=='record_curve':
                            if browser.selected is not None:browser.chart_record=browser.selected;page=0
                        elif key=='return_live':browser.chart_record=None
                        elif key=='cancel':
                            if live:controller.cancel()
                            elif job:job.cancel()
                        elif key=='frequency':
                            choices=args.sweep
                            index=min(range(len(choices)),key=lambda k:abs(choices[k]-selected))
                            selected=choices[max(0,min(len(choices)-1,index+value))]
                            if live:controller.select(selected)
                        elif key=='sweep':sweep=value
                        elif key=='position':
                            if not getattr(args,'prototype_mode',False):position=value
                        elif key in ('start','toggle_single','lifetime'):
                            clicked=time.monotonic()
                            if live:
                                if key=='toggle_single':controller.toggle_single()
                                else:
                                    controller.request_lifetime();ui_started=clicked;reported_output=None
                                controller.tick();job=controller.job;state=controller.snapshot()
                                state.update(context=metadata,prototype_mode=bool(getattr(args,'prototype_mode',False)))
                                if state.get('status')=='ERROR' and job is None:
                                    controller.state['ui_notice']=start_notice(state.get('error'))
                                    state['ui_notice']=controller.state['ui_notice']
                                print('UI_CONTROL',key,'single='+str(controller.single_running),flush=True)
                                continue
                            try:
                                hardware.stop()
                                if position!='normal' and (not args.pair_id or not args.confirm_zero_before_move):
                                    raise ValueError('A/B requires pair-id and move confirmation')
                                else:
                                    meta=dict(metadata,pair_id=args.pair_id,zero_ack_before_move=args.confirm_zero_before_move)
                                    job=MeasurementJob(hardware,profile,settings,
                                        tuple(args.sweep) if sweep else [selected],args.output,calibration,meta,
                                        position=position,model=model,budget_s=args.budget,
                                        current_zero=getattr(args,'current_zero_reference',None))
                                    job.start()
                                    state=job.snapshot()
                                    state.update(context=metadata,prototype_mode=bool(getattr(args,'prototype_mode',False)))
                                    ui_started=clicked;reported_output=None
                            except (ValueError,RuntimeError,OSError) as exc:
                                job=None
                                state=dict(status='ERROR',error=str(exc),result=None,source='hardware',
                                    ai=model.reject('start_failed'),ui_notice=start_notice(str(exc)))
                except OSError:
                    touch.close();touch=None
            history=browser.view() if page==2 else None;record=browser.chart_record if page==0 else None
            view=(state,page,selected,sweep,position,calibration is not None,args.sweep,history,record)
            if not live or view!=last_view:
                frame=hdmi.newframe()
                np.asarray(frame)[:]=painter.render(state,page,selected,sweep,position,calibration is not None,args.sweep,
                    history=history,record=record)
                hdmi.writeframe(frame)
                if live:last_view=deepcopy(view)
                if first_frame:
                    print('UI_FRAME_READY pages=3',flush=True);first_frame=False
                result_state=(controller.last_life or {}) if live else state
                if (ui_started is not None and page==0 and record is None
                        and result_state.get('status') in ('DONE','UNVERIFIED','ERROR','TIMEOUT','CANCELLED')
                        and result_state.get('output')!=reported_output):
                    print('UI_RESULT',result_state['status'],'click_to_frame_s=%.3f'%(time.monotonic()-ui_started),
                          'source='+str(result_state.get('source')),result_state.get('output'),flush=True)
                    reported_output=result_state.get('output')
            time.sleep(.004 if live else .04)
    finally:
        if live:controller.cancel();job=controller.job
        if job and job.thread and job.thread.is_alive():job.cancel();job.thread.join(timeout=2)
        hardware.stop()
        if touch:touch.close()
        hdmi.stop()

def main():
    args=parse()
    if args.command=='audit-session':
        from session_audit import audit_session
        json_write(args.output,audit_session(args.session,args.calibration));print(args.output);return 0
    if args.command=='export-candidate':
        from dataset_export import export_candidate
        json_write(args.output,export_candidate(args.session,args.calibration));print(args.output);return 0
    if args.command=='replay-model':
        model=HealthModel(args.model)
        sample=json.loads(args.input.read_text(encoding='utf-8-sig'))
        started=time.monotonic();ai=model.public_replay(sample)
        json_write(args.output,dict(source='public_dataset_replay',ai=ai,elapsed_s=time.monotonic()-started))
        print(args.output);return 0 if ai['status']=='research_estimate' else 2
    if args.command in ('calibration-template','calibration-check'):
        profile=HardwareProfile.load(args.profile).validate(arm=False)
        if args.command=='calibration-template':
            document=reference_template(profile,Settings(),args.sweep)
        else:
            document=calibration_coverage(Calibration.load(args.calibration),profile,Settings(),args.sweep)
        json_write(args.output,document)
        print(args.output)
        return 0 if args.command=='calibration-template' or document['complete'] else 2
    if args.command=='ab-export':
        json_write(args.output,export_ab_session(args.session,args.frequency,args.calibration));return 0
    if args.command=='calibrate-transfer':
        json_write(args.output,from_reference(json.loads(args.input.read_text(encoding='utf-8-sig'))));return 0
    if args.command=='ab-check':
        data=json.loads(args.input.read_text(encoding='utf-8-sig'))
        json_write(args.output,ab_check(data['a'],data['b'],data['standard']));return 0
    if args.command=='preview':
        from panel import Painter
        from ui_records import RecordBrowser,start_notice
        from PIL import Image
        painter=Painter();args.output.mkdir(parents=True,exist_ok=True)
        browser=RecordBrowser(args.output)
        for status in ('IDLE','CAPTURING','CANCELLED','ERROR','TIMEOUT','UNVERIFIED'):
            state=dict(status=status,frequency_hz=80,progress=.45,completed=2,total=6,result=None)
            Image.fromarray(painter.render(state)[...,::-1]).save(args.output/(status.lower()+'.png'))
        for page in (1,2):
            Image.fromarray(painter.render(dict(status='IDLE',result=None),page)[...,::-1]).save(args.output/('page%d.png'%page))
        Image.fromarray(painter.render(dict(status='IDLE',result=None),2,history=browser.view())[...,::-1]).save(args.output/'records.png')
        Image.fromarray(painter.render(dict(status='ERROR',result=None,error='physical module',
            ui_notice=start_notice('physical module')))[...,::-1]).save(args.output/'start_notice.png')
        model=HealthModel(BASE/'models/frozen_two_frequency_20261022/lib_two_ridge.json')
        sample=json.loads((BASE/'models/frozen_two_frequency_20261022/lib_two_ridge.example.json').read_text())
        for name,ai,source in [('research',model.public_replay(sample),'public_dataset_replay'),
                               ('domain_pending',model.reject('domain_requires_validation'),'simulation')]:
            state=dict(status='UNVERIFIED',source=source,result=None,ai=ai,frequency_hz=2.16)
            Image.fromarray(painter.render(state)[...,::-1]).save(args.output/(name+'.png'))
        print(args.output);return 0
    profile=HardwareProfile.load(args.profile).validate(arm=args.command!='simulate')
    settings=Settings(repeats=args.repeats).validate()
    frequencies=args.sweep or ([args.frequency] if args.frequency is not None else DEFAULT_SWEEP)
    args.sweep=list(frequencies)
    model=HealthModel(args.model)
    calibration=Calibration.load(args.calibration) if args.calibration else None
    current_zero=CurrentZeroReference.load(args.current_zero) if getattr(args,'current_zero',None) else None
    args.current_zero_reference=current_zero
    metadata=json.loads(args.metadata.read_text(encoding='utf-8-sig')) if args.metadata else {}
    if args.position!='normal' and (not args.pair_id or not args.confirm_zero_before_move):
        raise ValueError('A/B runs require pair-id and zero-before-move confirmation')
    metadata.update(pair_id=args.pair_id,zero_ack_before_move=args.confirm_zero_before_move)
    if args.command=='simulate':
        from synthetic import SyntheticHardware
        # Separate explicitly marked synthetic profile; it cannot pass overlay hash validation.
        profile=replace(profile,hardware_id='SIMULATION',board_serial='SIMULATION',module_serial='SIMULATION',
                        wiring_verified=True,module_verified=True,adc_clock_verified=True,polarity_verified=True,limits_confirmed=True)
        hardware=SyntheticHardware();overlay=None;source='simulation'
    else:
        if args.overlay is None:raise ValueError('--overlay required for hardware')
        validate_request(profile,settings,frequencies,calibration,'hardware',
                         args.position,args.budget,metadata,current_zero)
        from hardware import BatteryHardware
        hardware,overlay=BatteryHardware.load(args.overlay,profile);source='hardware'
    if args.command=='panel':
        panel_loop(hardware,overlay,args,profile,settings,calibration,metadata,model);return 0
    job=MeasurementJob(hardware,profile,settings,frequencies,args.output,calibration,metadata,source,args.position,
                       model=model,budget_s=args.budget,current_zero=current_zero)
    def cancel(_s,_f):job.cancel()
    signal.signal(signal.SIGINT,cancel);signal.signal(signal.SIGTERM,cancel)
    job.run()
    print(json.dumps(job.snapshot(),ensure_ascii=False,allow_nan=False))
    return 0 if job.snapshot()['status'] in ('DONE','UNVERIFIED') else 1

if __name__=='__main__':raise SystemExit(main())
