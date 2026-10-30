"""Explicit simulated UI fixtures for the three-page operator interface.

Renders 首页 / 设置 / 记录 only. No board access, no measurement, no model
claim: every battery, impedance and record below is a display fixture.
"""
import json
import math
from pathlib import Path
import sys
from PIL import Image

HERE=Path(__file__).resolve().parent
BASE=HERE.parent/'src/battery_eis'
sys.path[:0]=[str(BASE/'ps')]
from panel import Painter
from ui_records import RecordBrowser,start_notice


def demo_record(index=0,full=False):
    rows=[]
    for frequency in ([2.16,4,8,17.8,32,64,100] if full else [2.165324986,17.81154424]):
        z=.01+.03/(1+1j*2*math.pi*frequency*.01)
        for repeat in (0,1):
            rows.append(dict(frequency_hz=frequency,impedance_ohm=[z.real,z.imag],magnitude_ohm=abs(z),
                phase_deg=math.degrees(math.atan2(z.imag,z.real)),voltage_dc_v=3.52-index*.001,
                current_dc_a=.173,valid=False,quality_ok=True,calibration_id=None,
                repeat=repeat,source='simulation',reasons=['uncalibrated']))
    return dict(status='UNVERIFIED',source='simulation',results=rows,
        ai=dict(status='rejected',reason='domain_requires_validation',soh=None,capacity_Ah=None,rul=None),
        error=None,captures=[],schema='UI-DEMO-ONLY')


def write_demo(root,count=13):
    root.mkdir(parents=True,exist_ok=True)
    for index in range(count):
        folder=root/('20261027T%06dZ-demo-%04d'%(index,index))
        folder.mkdir(exist_ok=True)
        session=dict(source='simulation',schema='UI-DEMO-ONLY',utc_started='2026-10-27T12:%02d:00+00:00'%(index%60),
            profile={'cell_id':'Demo-20700-%03d'%index},context={'temperature_c':20.,'soc_fraction':.5})
        (folder/'session.json').write_text(json.dumps(session),encoding='utf-8')
        (folder/'summary.json').write_text(json.dumps(demo_record(index)),encoding='utf-8')
    (root/'DEMO_ONLY.txt').write_text('Simulated display fixtures; not measured spectra or model output.',encoding='utf-8')


def main():
    out=HERE/'previews';out.mkdir(exist_ok=True)
    data=HERE/'preview_records';write_demo(data)
    browser=RecordBrowser(data);painter=Painter()
    summary=demo_record()
    state=dict(status='UNVERIFIED',source='simulation',ai=summary['ai'],result=summary['results'][-1],
        spectrum=summary['results'],frequency_hz=17.81154424,context={'temperature_c':20.})
    def save(name,page=0,state_override=None,**kwargs):
        frame=painter.render(dict(state,**(state_override or {})),page=page,**kwargs)
        assert all('?' not in row[-1] for row in painter.bounds),painter.bounds
        image=Image.fromarray(frame[...,::-1]);image.save(out/(name+'.png'));return image
    images=[save('home_two_points')]
    full=demo_record(full=True)
    Image.fromarray(painter.render(dict(state,spectrum=full['results']))[...,::-1]).save(out/'home_sweep_demo.png')
    images.append(save('settings',1,sweep=True,position='A'))
    images.append(save('records_page_1',2,history=browser.view()))
    browser.move(3);save('records_last_page',2,history=browser.view())
    browser.move(-3);browser.open(0)
    images.append(save('record_detail',2,history=browser.view()))
    save('record_curve',0,record=browser.selected)
    empty=dict(status='IDLE',result=None,source='hardware',ai={})
    Image.fromarray(painter.render(empty)[...,::-1]).save(out/'home_empty.png')
    save('start_notice',0,state_override=dict(ui_notice=start_notice('physical module')))
    sheet=Image.new('RGB',(1600,1200),(24,26,29))
    for image,xy in zip(images+[Image.open(out/'home_sweep_demo.png')],[(0,0),(800,0),(0,600),(800,600)]):
        sheet.paste(image,xy)
    sheet.save(out/'overview.png')
    print('PREVIEWS_OK',out,'simulation only')


if __name__=='__main__':main()
