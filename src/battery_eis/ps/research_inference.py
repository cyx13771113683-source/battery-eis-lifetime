"""Portable research inference. Standard library only. No board integration."""
import argparse, json, math, struct
from pathlib import Path

def numerical_predict(model, features):
    x=[(float(v)-m)/s for v,m,s in zip(features,model['mean'],model['scale'])]
    e=model['estimator'];kind=e['kind']
    if kind=='ridge':return e['intercept']+sum(a*b for a,b in zip(e['coef'],x))
    if kind=='mlp':
        for layer,(w,b) in enumerate(zip(e['weights'],e['biases'])):
            x=[b[j]+sum(x[i]*w[i][j] for i in range(len(x))) for j in range(len(b))]
            if layer<len(e['weights'])-1:x=[math.tanh(v) for v in x]
        return x[0]
    if kind=='forest':
        # sklearn's tree predictor converts standardized input to float32.
        # Thresholds remain float64; skipping this cast can change a branch.
        x=[struct.unpack('f',struct.pack('f',v))[0] for v in x]
        ys=[]
        for t in e['trees']:
            i=0
            while t['left'][i]!=-1:i=t['left'][i] if x[t['feature'][i]]<=t['threshold'][i] else t['right'][i]
            ys.append(t['value'][i])
        return sum(ys)/len(ys)
    raise ValueError('unsupported model kind')

def predict(model, sample):
    """Return a research estimate or explicit rejection, never a board-valid SOH/RUL."""
    fail=lambda reason:dict(status='rejected',reason=reason,soh=None,capacity_Ah=None,rul=None,valid_for_board=False)
    if not isinstance(sample,dict):return fail('invalid_input')
    if sample.get('impedance_unit')!='ohm' or sample.get('convention')!='passive_Z_re_plus_j_im':return fail('unit_or_sign_convention')
    if sample.get('source_domain')!=model['source_domain']:return fail('domain_requires_validation')
    if sample.get('chemistry')!=model['chemistry']:return fail('unsupported_chemistry')
    if sample.get('measurement_valid') is not True or sample.get('calibration_valid') is not True:return fail('measurement_or_calibration_invalid')
    if sample.get('soc_percent')!=model['soc_percent']:return fail('SOC_outside_training_condition')
    temp=sample.get('temperature_C')
    if isinstance(temp,bool) or not isinstance(temp,(float,int)) or not math.isfinite(temp):return fail('temperature_required')
    lo,hi=model['measurement_temperature_C']
    if not lo<=temp<=hi:return fail('temperature_outside_training_condition')
    fs=sample.get('frequency_hz');r=sample.get('z_real_ohm');im=sample.get('z_imag_ohm')
    if not all(isinstance(a,list) and len(a)==len(model['frequencies_hz']) for a in [fs,r,im]):return fail('frequency_shape')
    if not all(isinstance(v,(float,int)) and not isinstance(v,bool) and math.isfinite(v) for a in [fs,r,im] for v in a):return fail('nonfinite_or_invalid_values')
    if any(abs(a-b)>max(.002,b*.0001) for a,b in zip(fs,model['frequencies_hz'])):return fail('frequency_mismatch')
    if any(v<=0 for v in r):return fail('nonpositive_real_impedance')
    x=r+im
    if any(v<a or v>b for v,a,b in zip(x,model['lower'],model['upper'])):return fail('outside_training_envelope')
    y=numerical_predict(model,x)
    if not math.isfinite(y) or not model['target_range'][0]<=y<=model['target_range'][1]:return fail('prediction_outside_training_range')
    cap=y*model['nominal_capacity_Ah'] if model.get('nominal_capacity_Ah') is not None else None
    return dict(status='research_estimate',reason='public_data_only',soh=y,soh_definition=model['target_definition'],capacity_Ah=cap,rul=None,valid_for_board=False)

def main():
    p=argparse.ArgumentParser();p.add_argument('model',type=Path);p.add_argument('input',type=Path);a=p.parse_args()
    print(json.dumps(predict(json.loads(a.model.read_text(encoding='utf8')),json.loads(a.input.read_text(encoding='utf8'))),ensure_ascii=False,allow_nan=False))

if __name__=='__main__':main()
