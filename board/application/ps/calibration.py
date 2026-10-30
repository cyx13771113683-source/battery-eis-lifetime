"""Measured per-channel transfer corrections, exact binding, independent A/B check."""
import json
import math
import copy
from pathlib import Path
from configuration import digest
from analysis import complex_value, pair

class Calibration:
    def __init__(self, document):
        self.document=copy.deepcopy(document)
        document=self.document
        if not isinstance(document.get('simulation_only',False),bool):
            raise ValueError('simulation_only must be a JSON boolean')
        if document.get('schema')!='battery-transfer-calibration/1' or not document.get('id'):
            raise ValueError('unknown calibration schema/id')
        if document.get('evidence_kind')!='independent_reference':
            raise ValueError('calibration requires independent reference evidence')
        if not document.get('reference_source') or not document.get('rows'):
            raise ValueError('reference source and frequency rows required')
        seen=set()
        for row in document['rows']:
            key=digest(row['binding'])
            if key in seen:raise ValueError('duplicate calibration binding')
            seen.add(key)
            for field in ('voltage_correction','current_correction'):
                if not .5<=abs(complex_value(row[field]))<=2:
                    raise ValueError('implausible channel correction')
            lo,hi=row['voltage_range_v'];ilo,ihi=row['current_ac_range_a']
            if not 0<lo<hi<=5 or not 0<ilo<ihi<=.55:
                raise ValueError('invalid calibration envelope')
            if not math.isfinite(row['reference_uncertainty_relative']) or not 0<row['reference_uncertainty_relative']<.2:
                raise ValueError('reference uncertainty required')

    @classmethod
    def load(cls,path):return cls(json.loads(Path(path).read_text(encoding='utf-8-sig')))

    def corrections(self,binding,vdc,iac):
        matches=[r for r in self.document['rows'] if r['binding']==binding]
        if len(matches)!=1:raise ValueError('calibration hardware/rate/range/frequency/waveform binding mismatch')
        row=matches[0]
        if not row['voltage_range_v'][0]<=vdc<=row['voltage_range_v'][1] or not row['current_ac_range_a'][0]<=iac<=row['current_ac_range_a'][1]:
            raise ValueError('measurement outside calibration voltage/current envelope')
        return (complex_value(row['voltage_correction']),complex_value(row['current_correction'])),self.document['id']

def from_reference(document):
    """Input measurements must use identical reference phase and actual I/V.

    Not solvable from a nominal 10mOhm resistor alone: two independent complex
    reference channels are required to determine two channel transfer factors.
    """
    if not isinstance(document.get('simulation_only',False),bool):
        raise ValueError('simulation_only must be a JSON boolean')
    if document.get('evidence_kind')!='independent_reference':raise ValueError('independent reference required')
    for row in document['rows']:
        if row.get('acquisition_supported') is False or row.get('binding') is None:
            raise ValueError('unsupported acquisition frequency: %s Hz'%row.get('requested_frequency_hz','unknown'))
        fields=('measured_voltage_ac_v','reference_voltage_ac_v','measured_current_ac_a',
                'reference_current_ac_a','voltage_range_v','current_ac_range_a','reference_uncertainty_relative')
        if any(row.get(field) is None for field in fields):
            raise ValueError('incomplete independent reference measurements at %s Hz'%row.get('requested_frequency_hz','unknown'))
    output=dict(schema='battery-transfer-calibration/1',id=document['id'],
                evidence_kind=document['evidence_kind'],reference_source=document['reference_source'],
                simulation_only=document.get('simulation_only',False) is True,
                reference_document_sha256=digest(document),rows=[])
    for row in document['rows']:
        v=complex_value(row['measured_voltage_ac_v']);i=complex_value(row['measured_current_ac_a'])
        if abs(v)<1e-6 or abs(i)<1e-5:raise ValueError('calibration excitation below usable floor')
        output['rows'].append(dict(binding=row['binding'],voltage_correction=pair(complex_value(row['reference_voltage_ac_v'])/v),
              current_correction=pair(complex_value(row['reference_current_ac_a'])/i),
              voltage_range_v=row['voltage_range_v'],current_ac_range_a=row['current_ac_range_a'],
              reference_uncertainty_relative=row['reference_uncertainty_relative']))
    Calibration(output)
    return output

def ab_check(a,b,standard):
    """Positive series standard stays in place; only S+ moves A->B after stop.

    Assumes linear, stationary port and unchanged discharge current transfer.
    Result is a verification report, never an automatically approved calibration.
    """
    if standard.get('independently_measured') is not True or not standard.get('reference_source'):
        raise ValueError('independently measured standard impedance required')
    if a.get('source') not in ('hardware','simulation') or a.get('source')!=b.get('source'):
        raise ValueError('A/B acquisition source differs or is missing')
    if a['position']!='A' or b['position']!='B' or a['pair_id']!=b['pair_id']:
        raise ValueError('A/B pair identity mismatch')
    if a['cell_id']!=b['cell_id'] or a['binding']!=b['binding']:
        raise ValueError('A/B cell or hardware settings differ')
    if not a.get('zero_ack_before_move') or not b.get('zero_ack_before_move'):
        raise ValueError('DA0 zero acknowledgements required')
    za=complex_value(a['result']['impedance_ohm']);zb=complex_value(b['result']['impedance_ohm'])
    ia=complex_value(a['result']['current_ac_a']);ib=complex_value(b['result']['current_ac_a'])
    if abs(ia)<1e-5 or abs(ib-ia)/abs(ia)>.02:raise ValueError('A/B current changed by more than 2%')
    if abs(a['result']['voltage_dc_v']-b['result']['voltage_dc_v'])>.01:
        raise ValueError('A/B DC shift too large; battery stationarity not established')
    if not a['result']['quality_ok'] or not b['result']['quality_ok']:raise ValueError('invalid A/B capture')
    if a['result'].get('calibration_id')!=b['result'].get('calibration_id'):
        raise ValueError('A/B calibration differs')
    if a.get('calibration_document_sha256')!=b.get('calibration_document_sha256'):
        raise ValueError('A/B calibration document differs')
    frequency=a['result']['frequency_hz']
    if frequency!=b['result']['frequency_hz']:
        raise ValueError('A/B result frequencies differ')
    if abs(frequency-standard['frequency_hz'])>max(.001,frequency*.0001):
        raise ValueError('standard frequency differs from acquisition')
    uncertainty=standard['uncertainty_relative']
    if not math.isfinite(uncertainty) or not 0<uncertainty<.2:
        raise ValueError('standard uncertainty required')
    zstd=complex_value(standard['impedance_ohm'])
    if abs(zstd)==0:raise ValueError('zero standard impedance')
    delta=zb-za;relative=abs(delta-zstd)/abs(zstd)
    return dict(schema='battery-ab-verification/1',pair_id=a['pair_id'],delta_ohm=pair(delta),
                standard_ohm=pair(zstd),relative_residual=relative,
                reference_source=standard['reference_source'],
                reference_uncertainty_relative=uncertainty,frequency_hz=frequency,
                source_a=a.get('source'),source_b=b.get('source'),
                calibrated=a['result'].get('calibration_id') is not None,
                interpretation='Z_B-Z_A, stationarity and Kelvin placement still require physical verification',
                creates_calibration=False,board_validated=False)

def export_ab_session(path,frequency_hz,calibration_path=None):
    """Export only recorded completed repeats; retain provenance and stop evidence."""
    from session_audit import audit_session
    path=Path(path)
    audit=audit_session(path,calibration_path)
    if not audit['completed'] or not audit['reconstructed_quality_ok']:
        raise ValueError('A/B export requires independently reconstructed quality and repeatability')
    session=json.loads((path/'session.json').read_text(encoding='utf-8-sig'))
    summary=json.loads((path/'summary.json').read_text(encoding='utf-8-sig'))
    if summary['status'] not in ('DONE','UNVERIFIED'):
        raise ValueError('A/B export requires a completed session')
    context=session['context']
    if not context.get('pair_id') or not context.get('zero_ack_before_move'):
        raise ValueError('pair identity and move confirmation missing')
    rows=[r for r in summary['results'] if abs(r['requested_frequency_hz']-frequency_hz)<1e-6]
    if len(rows)<2 or not all(r['quality_ok'] for r in rows):
        raise ValueError('A/B requires at least two good repeated captures')
    first=rows[0]
    for r in rows:
        if r['position'] not in ('A','B') or any(r[k]!=first[k] for k in ('binding','position','cell_id','calibration_id')):
            raise ValueError('inconsistent A/B capture metadata')
        capture=json.loads((path/r['raw_file'].replace('_raw.csv','_capture.json')).read_text(encoding='utf-8-sig'))
        if not capture.get('status',0)&16 or capture.get('faults'):
            raise ValueError('capture has no clean DA0 zero acknowledgement')
    result=dict(first)
    for field in ('impedance_ohm','voltage_ac_v','current_ac_a'):
        result[field]=pair(sum(complex_value(r[field]) for r in rows)/len(rows))
    result['voltage_dc_v']=sum(r['voltage_dc_v'] for r in rows)/len(rows)
    return dict(schema='battery-ab-point/1',pair_id=context['pair_id'],cell_id=first['cell_id'],
                binding=first['binding'],position=first['position'],zero_ack_before_move=True,
                source=summary['source'],session_path=str(path.resolve()),
                raw_reconstruction_passed=True,evidence_sha256=audit['evidence_sha256'],
                calibration_document_sha256=audit['calibration_document_sha256'],
                summary_sha256=__import__('hashlib').sha256((path/'summary.json').read_bytes()).hexdigest(),
                utc_started=session['utc_started'],utc_finished=summary['utc_finished'],
                repeat_count=len(rows),result=result)
