"""PL-moment solve and independent timestamp/harmonic diagnostics.

Phasors use x(t)=DC+Re(X*exp(j*phase)); values are peak, current discharge positive.
The battery observable is Z=-V/I. DAC command values never enter this ratio.
"""
import math
import numpy as np
from configuration import ABI,CLOCK_HZ,phase_increment

def pair(value): return [float(value.real),float(value.imag)]

def complex_value(value):
    if not isinstance(value,(list,tuple)) or len(value)!=2:
        raise ValueError('complex value must be [real, imaginary]')
    z=complex(*value)
    if not math.isfinite(abs(z)):raise ValueError('non-finite complex value')
    return z

def solve_moments(row):
    n,x,s,c,ss,cc,sc,xs,xc=map(float,row)
    if n<100:raise ValueError('insufficient samples')
    scale=8191.
    gram=np.array([[n,s/scale,c/scale],[s/scale,ss/scale**2,sc/scale**2],
                   [c/scale,sc/scale**2,cc/scale**2]])
    condition=float(np.linalg.cond(gram))
    if not math.isfinite(condition) or condition>100:
        raise ValueError('poor phase coverage')
    dc,si,co=np.linalg.solve(gram,np.array([x,xs/scale,xc/scale]))
    return float(dc),complex(co,-si),condition

def moments_from_raw(raw, lut):
    output=[]
    for channel in (0,1):
        r=raw[raw[:,3]==channel];x=r[:,2];idx=(r[:,1]>>22)&1023
        s=lut[idx].astype(np.int64);c=lut[(idx+256)&1023].astype(np.int64)
        output.append([len(r),int(x.sum()),int(s.sum()),int(c.sum()),int((s*s).sum()),
                       int((c*c).sum()),int((s*c).sum()),int((x*s).sum()),int((x*c).sum())])
    return tuple(output)

def diagnose(raw, channel, frequency):
    r=raw[raw[:,3]==channel]
    if len(r)<100:raise ValueError('channel has fewer than 100 samples')
    ticks=r[:,0]
    if np.any(np.diff(ticks)<=0):raise ValueError('timestamps not strictly increasing')
    t=ticks/1e8
    if (t[-1]-t[0])*frequency<7.8:raise ValueError('fewer than eight capture cycles')
    phase=r[:,1]*(2*np.pi/2**32)
    # Include drift, odd square harmonics, and mains as nuisance terms. At exact
    # overlaps do not add duplicate columns. This is a diagnostic, not a claim
    # that out-of-band aliasing can be removed in software.
    columns=[np.ones(len(t)),(t-t.mean())/(t[-1]-t[0]),np.cos(phase),np.sin(phase)]
    fitted_freqs=[frequency]
    sample_rate=1/float(np.median(np.diff(t)))
    for harmonic in (3,5):
        if harmonic*frequency<.45*sample_rate:
            columns.extend([np.cos(harmonic*phase),np.sin(harmonic*phase)])
            fitted_freqs.append(harmonic*frequency)
    for mains in (50.,60.):
        if min(abs(mains-f) for f in fitted_freqs)>2/(t[-1]-t[0]):
            columns.extend([np.cos(2*np.pi*mains*t),np.sin(2*np.pi*mains*t)])
    matrix=np.column_stack(columns)
    beta,_,rank,_=np.linalg.lstsq(matrix,r[:,2],rcond=None)
    if rank!=matrix.shape[1] or np.linalg.cond(matrix)>100:
        raise ValueError('ill-conditioned timestamp fit')
    residual=r[:,2]-matrix@beta
    variance=float(residual@residual/max(1,len(t)-rank))
    covariance=np.linalg.inv(matrix.T@matrix)*variance
    sigma=math.sqrt(max(covariance[2,2]+covariance[3,3],0))
    # Descriptive only: this does not certify IID noise or physical accuracy.
    residual_energy=float(residual@residual)
    roundoff_floor=100*np.finfo(float).eps*max(1.,float(np.max(np.abs(r[:,2]))))
    residual_lag1=0.
    if residual_energy>0 and math.sqrt(variance)>roundoff_floor:
        residual_lag1=float(residual[1:]@residual[:-1]/residual_energy)
    phasor=complex(beta[2],-beta[3])
    return dict(phasor=phasor,sigma_codes=sigma,residual_rms_codes=math.sqrt(variance),
                sample_rate_hz=sample_rate,dc_drift_codes=float(beta[1]),n=len(t),
                maximum_gap_s=float(np.diff(t).max()),
                residual_lag1_correlation=residual_lag1)

def analyze(capture, settings, profile, calibration=None):
    status=capture.meta.get('status',0)
    if status>>16!=ABI or status&0x16!=0x14:
        raise ValueError('wrong ABI or capture not frozen with DA0 zero')
    expected=[phase_increment(settings.frequency_hz),round(settings.settle_s*CLOCK_HZ),
              round(settings.capture_s*CLOCK_HZ),settings.gate_high_code]
    if list(capture.meta.get('configuration',[]))!=expected:
        raise ValueError('actual capture configuration differs from requested binding')
    adc=capture.meta.get('adc_registers',[])
    if len(adc)!=4 or adc[0]&0xfffffffe!=0xf0000130 or adc[3]!=32768:
        raise ValueError('actual ADC configuration differs from BE03 binding')
    raw=np.asarray(capture.raw,dtype=np.int64)
    if raw.ndim!=2 or raw.shape[1]!=4 or len(raw)==0:raise ValueError('empty/malformed raw capture')
    if np.any((raw[:,3]!=0)&(raw[:,3]!=1)):raise ValueError('unknown channel')
    if np.any(np.diff(raw[:,0])<=0) or np.any(np.diff(raw[:,3])==0):
        raise ValueError('lost/duplicate/nonalternating samples')
    if np.any(raw[:,2]>=8388607) or np.any(raw[:,2]<=-8388608):raise ValueError('ADC saturation')
    if np.any(raw[:,1]!=(raw[:,0]*expected[0])%(2**32)):
        raise ValueError('sample phase does not match hardware timestamp')
    for channel in (0,1):
        if int(capture.moments[channel][0])!=int((raw[:,3]==channel).sum()):
            raise ValueError('PL moment count differs from raw count')
    vdc,v,vc=solve_moments(capture.moments[0])
    idc,i,ic=solve_moments(capture.moments[1])
    diag=[diagnose(raw,ch,settings.actual_frequency_hz) for ch in (0,1)]
    vscale=profile.code_volts*profile.voltage_divider
    iscale=profile.code_volts/profile.r_shunt_ohm
    v*=vscale;i*=iscale
    vdc*=vscale*profile.voltage_dc_scale;idc*=iscale*profile.current_dc_scale
    reasons=[]
    if capture.meta.get('faults'):reasons.append('pl_fault_%x'%capture.meta['faults'])
    if capture.meta.get('status',0)&64:reasons.append('pl_moments_invalid')
    if not profile.minimum_voltage_v<=vdc<=profile.maximum_voltage_v:reasons.append('voltage_outside_profile')
    if not 0<=idc<=profile.maximum_current_a:reasons.append('current_outside_profile')
    corrections=(1+0j,1+0j);calibration_id=None
    if calibration is not None:
        corrections,calibration_id=calibration.corrections(profile.binding(settings),vdc,abs(i))
    v*=corrections[0];i*=corrections[1]
    v_sigma=diag[0]['sigma_codes']*vscale*abs(corrections[0])
    i_sigma=diag[1]['sigma_codes']*iscale*abs(corrections[1])
    for ch,label in ((0,'voltage'),(1,'current')):
        dc,pl,_=solve_moments(capture.moments[ch]);q=diag[ch]
        if abs(q['phasor'])<10*q['sigma_codes']:reasons.append(label+'_weak_signal')
        if abs(pl-q['phasor'])>max(3*q['sigma_codes'],.03*abs(q['phasor'])):
            reasons.append(label+'_harmonic_or_drift_bias')
        if q['maximum_gap_s']>4/q['sample_rate_hz']:reasons.append(label+'_sample_gap')
    if abs(i)<max(1e-5,10*i_sigma):reasons.append('insufficient_current')
    z=-v/i if abs(i)>1e-12 else None
    uncertainty=(abs(z)*math.hypot(v_sigma/max(abs(v),1e-30),i_sigma/max(abs(i),1e-30))) if z is not None else None
    if z is not None and abs(v)>.01:reasons.append('voltage_perturbation_over_10mV')
    quality_ok=not reasons
    if calibration is None:reasons.append('uncalibrated')
    diag_json=[]
    for q in diag:
        diag_json.append(dict(q,phasor=pair(q['phasor'])))
    return dict(frequency_hz=settings.actual_frequency_hz,requested_frequency_hz=settings.frequency_hz,
                voltage_dc_v=vdc,current_dc_a=idc,voltage_ac_v=pair(v),current_ac_a=pair(i),
                impedance_ohm=pair(z) if z is not None else None,
                magnitude_ohm=abs(z) if z is not None else None,
                phase_deg=math.degrees(np.angle(z)) if z is not None else None,
                random_uncertainty_ohm=uncertainty,valid=quality_ok and calibration is not None,
                quality_ok=quality_ok,reasons=reasons,calibration_id=calibration_id,
                diagnostics=diag_json,counts=[int(x[0]) for x in capture.moments],
                estimator='PL DC/sin/cos moments solved on PS',
                uncertainty_scope='random residual only; excludes systematic calibration, drift and aliasing',
                ai={'status':'no_validated_battery_model','soh':None,'rul':None})

def repeatability(results):
    good=[r for r in results if r['quality_ok'] and r['impedance_ohm'] is not None]
    if len(good)<2:return {'count':len(good),'std_real_ohm':None,'std_imag_ohm':None,'relative_std':None}
    z=np.array([complex(*r['impedance_ohm']) for r in good])
    spread=float(np.std(z,ddof=1))
    return dict(count=len(z),mean_ohm=pair(z.mean()),std_real_ohm=float(np.std(z.real,ddof=1)),
                std_imag_ohm=float(np.std(z.imag,ddof=1)),relative_std=spread/max(abs(z.mean()),1e-30))
