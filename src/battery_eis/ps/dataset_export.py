"""Export an audited session as an unlabelled candidate, never training truth."""
from pathlib import Path
import numpy as np
from configuration import digest
from session_audit import audit_session,load_json


def export_candidate(directory,calibration_path=None):
    path=Path(directory).resolve()
    audit=audit_session(path,calibration_path)
    if not audit['recorded_measurement_valid']:
        raise ValueError('session must pass independent reconstruction and quality checks')
    session=load_json(path/'session.json');summary=load_json(path/'summary.json')
    context=session['context'];profile=session['profile'];results=summary['results']
    if context.get('port_kind','battery_terminal')!='battery_terminal' or any(r.get('position')!='normal' for r in results):
        raise ValueError('A/B standards and regulated sources are not battery dataset candidates')
    frequencies=sorted({r['frequency_hz'] for r in results})
    spectra=[]
    for frequency in frequencies:
        rows=[r for r in results if r['frequency_hz']==frequency]
        values=np.array([complex(*r['impedance_ohm']) for r in rows])
        mean=values.mean()
        spectra.append(dict(frequency_hz=frequency,
            requested_frequency_hz=rows[0]['requested_frequency_hz'],
            impedance_ohm=[float(mean.real),float(mean.imag)],repeats=len(rows),
            repeat_std_re_im_ohm=[float(values.real.std(ddof=1)),float(values.imag.std(ddof=1))] if len(rows)>1 else [None,None],
            per_repeat_impedance_ohm=[r['impedance_ohm'] for r in rows],
            raw_files=[r['raw_file'] for r in rows],binding=rows[0]['binding'],
            calibration_id=rows[0]['calibration_id']))
    source=session['source']
    features=dict(source_domain='battery_eis_rev_a' if source=='hardware' else 'simulation',
        chemistry=profile['chemistry'],frequency_hz=frequencies,
        z_real_ohm=[s['impedance_ohm'][0] for s in spectra],
        z_imag_ohm=[s['impedance_ohm'][1] for s in spectra],
        impedance_unit='ohm',convention='passive_Z_re_plus_j_im',
        soc_percent=context['soc_fraction']*100 if context.get('soc_fraction') is not None else None,
        temperature_C=context.get('temperature_c'))
    return dict(schema='battery-eis-dataset-candidate/1',
        candidate_id=digest(dict(files=audit['evidence_sha256'],calibration=audit['calibration_document_sha256'])),
        source=source,session_directory=str(path),cell_id=profile['cell_id'],
        split_group_cell_id=profile['cell_id'],aging_group=context.get('aging_group'),
        utc_started=session['utc_started'],context_recorded=context,
        hardware_profile=profile,features=features,spectra=spectra,
        labels=dict(Q_ref_Ah=None,Q0_ref_Ah=None,SOH_initial=None,rul=None),
        training_eligible=False,target_domain_validated=False,
        pending=['independent physical measurement acceptance',
                 'independent capacity test with instrument, protocol and timestamp',
                 'capacity/EIS pairing and cell/aging-group split review'],
        audit=audit,
        note='Repeat spread is descriptive, not absolute uncertainty. Manual capacity metadata and model outputs are not imported as labels.')
