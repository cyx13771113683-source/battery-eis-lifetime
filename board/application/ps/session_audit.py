"""Read-only reconstruction of a saved session for data-integrity review.

This checks recorded files against each other. It cannot certify their physical
origin, the accuracy of the reference instrument, or battery health labels.
"""
from dataclasses import replace
import csv
import hashlib
import json
from pathlib import Path
import re
import numpy as np
from configuration import ABI, SCHEMA, RAW_CAPACITY, HardwareProfile, Settings, digest
from hardware import Capture
from analysis import analyze, moments_from_raw, repeatability
from calibration import Calibration
from current_zero import CurrentZeroReference,evaluate_zero_drive,_utc


def load_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _read_raw(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        rows=list(csv.reader(stream))
    if not rows or rows[0] != ['sample_index','drdy_tick_100MHz','phase_u32','adc_signed24','channel']:
        raise ValueError('raw CSV header mismatch')
    data=np.array([[int(v) for v in row] for row in rows[1:]], dtype=np.int64)
    if data.ndim!=2 or data.shape[1]!=5 or not 0<len(data)<=RAW_CAPACITY:
        raise ValueError('raw CSV shape/capacity mismatch')
    if not np.array_equal(data[:,0],np.arange(len(data))):
        raise ValueError('raw CSV index is missing or duplicated')
    raw=data[:,1:]
    if np.any(np.diff(raw[:,0])<=0) or np.any(np.diff(raw[:,3])==0):
        raise ValueError('raw time/channel order mismatch')
    if np.any((raw[:,3]<0)|(raw[:,3]>1)) or np.any((raw[:,2]<=-8388608)|(raw[:,2]>=8388607)):
        raise ValueError('raw channel invalid or ADC saturated')
    return raw


def audit_session(directory, calibration_path=None):
    path=Path(directory).resolve()
    files={}
    def tracked(name):
        file=path/name
        if file.resolve().parent!=path:raise ValueError('session entry must be a local file')
        files[name]=hashlib.sha256(file.read_bytes()).hexdigest()
        return file
    session=load_json(tracked('session.json'))
    summary=load_json(tracked('summary.json'))
    plan=load_json(tracked('measurement_plan.json'))
    if session.get('schema')!=SCHEMA or summary.get('schema')!=SCHEMA:
        raise ValueError('session ABI/schema mismatch')
    source=session.get('source')
    if source not in ('hardware','simulation') or summary.get('source')!=source:
        raise ValueError('session source mismatch')
    profile=HardwareProfile(**session['profile']).validate()
    settings=Settings(**session['settings']).validate()
    zero_reference=None;zero_offset_a=0.
    zero_path=path/'current_zero_used.json'
    if zero_path.is_file():
        zero_saved=load_json(tracked(zero_path.name))
        if digest(zero_saved['document'])!=zero_saved.get('document_sha256'):
            raise ValueError('current zero reference snapshot hash mismatch')
        zero_reference=CurrentZeroReference(zero_saved['document'])
    planned=[Settings(**s).validate() for s in plan['frequency_settings']]
    if not planned or len({digest(profile.binding(s)) for s in planned})!=len(planned):
        raise ValueError('duplicate or empty measurement plan')
    calibration=None
    snapshot_path=path/'calibration_used.json'
    if snapshot_path.is_file():
        saved=load_json(tracked('calibration_used.json'))
        if saved['document_sha256']!=digest(saved['document']):
            raise ValueError('calibration snapshot hash mismatch')
        calibration=Calibration(saved['document'])
    if calibration_path is not None:
        supplied=Calibration.load(calibration_path)
        if calibration and digest(supplied.document)!=digest(calibration.document):
            raise ValueError('external calibration differs from session snapshot')
        calibration=supplied
    if source=='hardware' and calibration and calibration.document.get('simulation_only') is True:
        raise ValueError('hardware session uses simulation-only calibration')
    stems=summary['captures']
    if not stems or len(set(stems))!=len(stems) or any(not isinstance(s,str) or not re.fullmatch(r'\d{3}',s) for s in stems):
        raise ValueError('invalid or duplicate capture list')
    if stems!=['%03d'%i for i in range(len(stems))]:raise ValueError('capture sequence incomplete')
    results=summary['results']
    result_map={r['raw_file']:r for r in results}
    if len(result_map)!=len(results):raise ValueError('duplicate result raw_file')
    if any(name not in [s+'_raw.csv' for s in stems] for name in result_map):
        raise ValueError('result references an absent capture')
    lut_path=Path(__file__).resolve().parents[1]/'rtl/sine14.mem'
    lut=np.array([int(x,16) for x in lut_path.read_text().split()],dtype=np.int64)
    lut=np.where(lut>=8192,lut-16384,lut)
    checked=[];sequences=[];reconstructed_rows=[]
    for index,stem in enumerate(stems):
        raw=_read_raw(tracked(stem+'_raw.csv'))
        meta=load_json(tracked(stem+'_capture.json'))
        status=meta.get('status',0)
        if status>>16!=ABI or status&0x16!=0x14 or meta.get('faults'):
            raise ValueError('capture not a clean frozen zero-drive record: '+stem)
        if meta.get('source')!=source or meta.get('raw_file')!=stem+'_raw.csv':
            raise ValueError('capture source/name mismatch: '+stem)
        if meta.get('hardware_signature')!=profile.hardware_id:
            raise ValueError('capture hardware identity mismatch: '+stem)
        sequence=meta.get('sequence')
        if sequence!=((status>>8)&255) or (sequences and sequence!=(sequences[-1]+1)%256):
            raise ValueError('stale or skipped capture sequence: '+stem)
        sequences.append(sequence)
        computed=moments_from_raw(raw,lut)
        if not np.array_equal(np.asarray(computed),np.asarray(meta['moments'])):
            raise ValueError('raw data disagree with PL moments: '+stem)
        if index==0:
            selected=replace(settings,frequency_hz=80.,settle_s=.2,capture_s=.2)
            if meta.get('position')!='preflight':raise ValueError('first capture must be preflight')
            saved=load_json(tracked(stem+'_result.json'))
            if saved.get('kind')!='zero_drive_preflight' or saved.get('valid') is not True:
                raise ValueError('preflight result mismatch')
            zero=evaluate_zero_drive(Capture(raw,tuple(meta['moments']),meta),profile,zero_reference,source,
                at_utc=_utc(meta['utc_saved']),settings=settings)
            if not zero['valid']:raise ValueError('preflight values outside accepted limits')
            if 'policy' in saved:
                if digest(saved)!=digest(zero):raise ValueError('preflight diagnostics differ from raw reconstruction')
            else:
                if zero_reference is not None:raise ValueError('legacy preflight cannot use zero reference')
                np.testing.assert_allclose([saved['voltage_v'],saved['leakage_a']],
                    [zero['voltage_v'],zero['raw_mean_a']],rtol=1e-10,atol=1e-12)
            zero_offset_a=zero['offset_applied_a']
        else:
            matches=[s for s in planned if profile.binding(s)==meta['binding']]
            if len(matches)!=1:raise ValueError('capture has no unique planned binding: '+stem)
            selected=matches[0]
            stored=result_map.get(stem+'_raw.csv')
            if stored is None:raise ValueError('capture result absent: '+stem)
            disk_result=load_json(tracked(stem+'_result.json'))
            if stored.get('calibration_id') and calibration is None:
                raise ValueError('calibrated session needs its original calibration file')
            reconstructed=analyze(Capture(raw,tuple(meta['moments']),meta),selected,profile,calibration)
            zero_fields={}
            if zero_reference is not None:
                zero_fields=dict(current_dc_raw_a=reconstructed['current_dc_a'],current_dc_offset_a=zero_offset_a,
                    current_dc_corrected_a=reconstructed['current_dc_a']-zero_offset_a,
                    current_zero_reference_sha256=digest(zero_reference.document))
            reconstructed_rows.append(dict(reconstructed,binding=meta['binding']))
            for candidate in (stored,disk_result):
                for key in ('current_dc_raw_a','current_dc_offset_a','current_dc_corrected_a','current_zero_reference_sha256'):
                    if candidate.get(key)!=zero_fields.get(key):
                        raise ValueError('saved current zero correction differs from reconstruction: '+stem)
                for key in ('impedance_ohm','voltage_ac_v','current_ac_a','voltage_dc_v','current_dc_a','frequency_hz'):
                    np.testing.assert_allclose(candidate[key],reconstructed[key],rtol=1e-9,atol=1e-12,
                        err_msg='saved result differs from reconstruction: '+stem+' '+key)
                if candidate.get('binding')!=profile.binding(selected) or candidate.get('source')!=source or candidate.get('cell_id')!=profile.cell_id or candidate.get('position')!=meta.get('position') or candidate.get('requested_frequency_hz')!=selected.frequency_hz:
                    raise ValueError('saved result provenance differs: '+stem)
                if candidate.get('calibration_id')!=reconstructed.get('calibration_id'):
                    raise ValueError('saved calibration identity differs: '+stem)
                if candidate.get('valid') is True and not reconstructed['valid']:
                    raise ValueError('invalid reconstruction was marked valid: '+stem)
                if candidate.get('quality_ok') is True and not reconstructed['quality_ok']:
                    raise ValueError('failed reconstructed quality was marked good: '+stem)
                # Old recordings predate lag-one diagnostics. When the field
                # is present, it must match the same raw timestamp fit; it is
                # descriptive and never changes calibration or validity.
                for channel, diagnostic in enumerate(candidate.get('diagnostics', [])):
                    if 'residual_lag1_correlation' in diagnostic:
                        if channel >= len(reconstructed['diagnostics']):
                            raise ValueError('extra saved diagnostic channel: '+stem)
                        np.testing.assert_allclose(diagnostic['residual_lag1_correlation'],
                            reconstructed['diagnostics'][channel]['residual_lag1_correlation'],
                            rtol=1e-9,atol=1e-12,
                            err_msg='saved residual correlation differs from reconstruction: '+stem)
        if meta['binding']!=profile.binding(selected):raise ValueError('capture binding differs: '+stem)
        expected=[meta['binding']['phase_increment'],meta['binding']['settle_ticks'],meta['binding']['capture_ticks'],meta['binding']['gate_high_code']]
        if list(meta.get('configuration',[]))!=expected:raise ValueError('configuration differs: '+stem)
        if np.any(raw[:,1]!=(raw[:,0]*expected[0])%(2**32)):
            raise ValueError('raw phase disagrees with timestamp: '+stem)
        checked.append(dict(stem=stem,records=len(raw),sequence=sequence,all_18_moments_match=True))
    expected_count=sum(s.repeats for s in planned)
    completed=summary.get('status') in ('DONE','UNVERIFIED') and len(results)==expected_count
    repeats_ok=all(sorted(r['repeat'] for r in results if r['binding']==profile.binding(s))==list(range(s.repeats)) for s in planned)
    if not repeats_ok:raise ValueError('missing or duplicate repeat identity')
    spreads=[repeatability([r for r in reconstructed_rows if r['binding']==profile.binding(s)]) for s in planned]
    quality_good=all(r['quality_ok'] for r in reconstructed_rows) and all(
        s['relative_std'] is not None and s['relative_std']<=.05 for s in spreads)
    independently_good=quality_good and all(r['valid'] for r in reconstructed_rows)
    if summary['status']=='DONE' and (not completed or not independently_good):
        raise ValueError('DONE session fails independent quality/repeatability reconstruction')
    good=completed and summary['status']=='DONE' and independently_good and all(r.get('valid') is True and r.get('quality_ok') is True for r in results)
    return dict(schema='battery-session-audit/1',data_integrity_ok=True,source=source,
        session=str(path),status=summary['status'],captures=checked,results=len(results),
        calibration_document_sha256=digest(calibration.document) if calibration else None,
        completed=completed,recorded_measurement_valid=good,repeatability=spreads,
        reconstructed_quality_ok=completed and quality_good,
        eligible_for_hardware_dataset_review=good and source=='hardware' and all(r['position']=='normal' for r in results),
        evidence_sha256=files,physical_accuracy_validated=False,
        note='Internal consistency only; physical source, reference accuracy, SOC and capacity labels still need independent evidence.')
