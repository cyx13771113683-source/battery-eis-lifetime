"""800x600 panel preserving the approved graphite/cyan/blue composition.

The operator UI has exactly three pages: home, settings and records. The
former calibration and status pages are gone; the content they carried that
is still required (calibration state, A/B contact position, measurement
status and failure reasons) now lives on settings and home, so deleting the
pages does not hide any measurement information.

2026-10-27, user decision: the 扫频 page was renamed 设置 and the 校准 and
状态 pages were deleted. Do not restore them from the dated archives under
build/, reports/ui_deploy_20261027/, reports/ui_start_feedback_20261027/ or
deliverables/ui_spectra_records_20261027/ - those are evidence snapshots, not
code to fix. See ../reports/ui_three_pages_20261027/README.md and the
workspace AGENTS.md section "ui-three-pages".

NumPy glyph atlas keeps PIL/font discovery off the embedded runtime path.
"""
from pathlib import Path
import numpy as np
from configuration import DEFAULT_SWEEP
from ui_records import UI_LABELS,health_values,source_label,spectrum_points,number,record_status_label,record_calibration_label

BG=(29,26,24);SURFACE=(48,42,36);DARK=(37,33,29);TEXT=(247,244,241)
MUTED=(204,195,184);CYAN=(232,201,72);BLUE=(255,181,123);AI=(63,43,29)
BORDER=(85,81,77);ORANGE=(88,171,255)
LABELS='电池阻抗首页设置记录电压电流复相位测量进度开始取消已停止准备未接入中自检采集分析完成待核验错误超时正在清理数据保存模型未配置物理接线确认当前温度未知标准件选择单点上一下一重复次正常外置串联先停励后移动仅正极取样线位置返回端保持不动内部不等于整机标定禁止单独接无源电阻先完成再停机记录有效低信号请查看详情仿真已标定未标定外部核验幅值原始信号拒绝无寿命结果重新测量配置文件初始化未就绪传感器'
LABELS+=' 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz.,:+-/%()=[]_°Ωμ→：'
LABELS+=UI_LABELS
STATUS={'IDLE':'准备就绪','INITIALIZING':'初始化','PREFLIGHT':'自检中','CAPTURING':'采集中',
        'ANALYZING':'分析中','CANCELLING':'正在停止','DONE':'测量完成','UNVERIFIED':'待核验',
        'ERROR':'测量错误','TIMEOUT':'采集超时','CANCELLED':'已取消'}
BUSY={'INITIALIZING','PREFLIGHT','CAPTURING','ANALYZING','CANCELLING'}
NAV=('首页','设置','记录')
NAV_RECTS=tuple((16+i*260,540,264+i*260,594) for i in range(3))

def ai_label(ai):
    ai=ai or {}
    if ai.get('status')=='prototype_result' and ai.get('soh') is not None:
        return 'SOH健康度 %.1f%%'%(100*ai['soh'])
    if ai.get('status')=='research_estimate' and ai.get('soh') is not None:
        return '研究 SOH %.1f%%'%(100*ai['soh'])
    if ai.get('reason')=='awaiting_measurement':return '模型已载入'
    if ai.get('reason')=='domain_requires_validation':return '目标电池待验证'
    if ai.get('status')=='rejected':return '数据待核验'
    return '模型未配置'

class Painter:
    def __init__(self,atlas=None):
        # NpzFile indexing decompresses each glyph again, including width checks.
        # Decode once before the UI loop; pixels and all layout rules stay exact.
        with np.load(atlas or Path(__file__).resolve().parents[1]/'assets/glyphs.npz',allow_pickle=False) as archive:
            self.atlas={key:archive[key] for key in archive.files}
        self.bounds=[]

    def width(self,text,size):return sum(self.atlas['%d_%d'%(size,ord(c))].shape[1] for c in text)

    def text(self,frame,x,y,text,size=24,color=TEXT,width=750):
        text=str(text)
        # Missing user metadata chars use '?' rather than silently disappearing.
        text=''.join(c if '%d_%d'%(size,ord(c)) in self.atlas else '?' for c in text)
        while text and self.width(text,size)>width:text=text[:-1]
        self.bounds.append((x,y,x+self.width(text,size),y+size+8,text))
        for char in text:
            mask=self.atlas['%d_%d'%(size,ord(char))]
            h,w=mask.shape
            if x<0 or y<0 or x+w>800 or y+h>600:raise ValueError('text outside frame')
            alpha=mask.astype(np.uint16)[...,None]
            region=frame[y:y+h,x:x+w]
            region[:]=(region.astype(np.uint16)*(255-alpha)+np.array(color,dtype=np.uint16)*alpha+127)//255
            x+=w

    def button(self,frame,rect,label,active=False):
        x,y,x1,y1=rect;frame[y:y1,x:x1]=SURFACE if active else DARK
        frame[y:y+2,x:x1]=CYAN if active else BORDER;frame[y1-2:y1,x:x1]=CYAN if active else BORDER
        self.text(frame,x+(x1-x-self.width(label,24))//2,y+(y1-y-32)//2,label,color=CYAN if active else TEXT,width=x1-x)

    def render(self,state,page=0,selected_hz=80,sweep=False,position='normal',calibrated=False,frequencies=DEFAULT_SWEEP,history=None,record=None):
        if page not in (0,1,2):raise ValueError('operator UI has exactly three pages')
        live_busy=state.get('status') in BUSY
        if record is not None:
            state=dict(record,viewing_record=True)
            calibrated=record.get('calibrated',False)
        frame=np.empty((600,800,3),dtype=np.uint8);frame[:]=BG;frame[:70]=DARK;self.bounds=[]
        self.text(frame,20,20,'电池阻抗',24,width=210)
        header_frequency=(history or {}).get('selected') if page==2 else None
        header_frequency=(header_frequency or state).get('frequency_hz',selected_hz)
        self.text(frame,300,22,('%g Hz'%header_frequency),24,CYAN,width=200)
        busy=live_busy
        self.button(frame,(646,10,784,60),'取消' if busy else '已停止',busy)
        for index,label in enumerate(NAV):self.button(frame,NAV_RECTS[index],label,index==page)
        result=state.get('result')
        def value(key,fmt,factor=1):
            val=result.get(key) if result else None
            return '--' if not number(val) else format(val*factor,fmt)
        if page==0:
            for x,x1,label,val in [(16,392,'电池电压',value('voltage_dc_v','.4f')+' V'),
                                  (404,784,'放电电流',value('current_dc_a','.3f',1000)+' mA')]:
                frame[82:168,x:x1]=SURFACE;frame[82:83,x:x1]=BORDER
                self.text(frame,x+20,87,label,20,MUTED,width=x1-x-40)
                self.text(frame,x+20,119,val,32,CYAN,width=x1-x-40)
            rows=state.get('spectrum') or ([result] if result else [])
            points=spectrum_points(rows)
            self.graph(frame,(16,180,392,384),'幅值 |Z| / mΩ',points,'magnitude_mohm')
            self.graph(frame,(404,180,784,384),'相位 / °',points,'phase_deg')
            frame[398:522,16:784]=AI;frame[398:522,16:21]=BLUE
            health=health_values(state.get('ai'))
            self.text(frame,36,405,'寿命结果',24,BLUE,width=160)
            self.text(frame,206,405,health['soh'],24,BLUE,width=330)
            self.text(frame,36,445,health['capacity'],20,MUTED,width=235)
            self.text(frame,548,408,health['rul'],20,BLUE,width=216)
            context=state.get('context') or {}
            temperature=context.get('temperature_c')
            self.text(frame,290,445,'温度 '+('%.1f°C'%temperature if number(temperature) else '未知'),20,MUTED,width=235)
            # The former status page is gone, so home carries status and reason.
            error=state.get('error')
            if error:
                self.text(frame,36,483,error,20,ORANGE,width=490)
            else:
                label=STATUS.get(state.get('status','IDLE'),'待核验')
                if busy:label+=' %d/%d'%(state.get('completed',0),state.get('total',0))
                if record is not None:label='历史记录'
                calibration_label=('已校正' if calibrated or state.get('ratio_corrected') else '未校正') if state.get('prototype_mode') else ('已标定' if calibrated else '未标定')
                self.text(frame,36,483,label+' / '+calibration_label+' / '+source_label(state.get('source')),20,width=490)
            if record is not None:self.button(frame,(548,458,764,514),'返回实时',True)
            elif not busy:self.button(frame,(548,458,764,514),'开始测量',True)
            else:
                self.text(frame,548,470,'%d%%'%round(100*state.get('progress',0)),24,CYAN,width=210)
            tag='历史' if record is not None else {'simulation':'仿真','public_dataset_replay':'回放'}.get(state.get('source'))
            if tag:self.text(frame,230,22,tag,20,ORANGE,width=65)
        elif page==1:
            self.text(frame,20,84,'设置',24)
            self.text(frame,520,87,('校正：'+('已载入' if calibrated else '--')) if state.get('prototype_mode') else '标定：'+('已标定' if calibrated else '未标定'),20,CYAN,width=264)
            frame[120:248,16:784]=SURFACE
            self.text(frame,36,130,'单点频率',20,MUTED,width=350)
            self.text(frame,36,166,'%g Hz'%selected_hz,32,CYAN,width=350)
            self.button(frame,(420,142,578,202),'上一点');self.button(frame,(600,142,764,202),'下一点')
            self.text(frame,36,212,' / '.join('%g'%f for f in frequencies)+' Hz',20,MUTED,width=720)
            self.text(frame,36,258,'测量模式',20,MUTED,width=350)
            self.button(frame,(16,284,392,336),'单点',not sweep)
            self.button(frame,(404,284,784,336),'扫频',sweep)
            if state.get('prototype_mode'):
                self.text(frame,36,362,'校正参数：电脑端维护',24,MUTED,width=720)
                self.text(frame,36,436,'正常四线接法',20,MUTED,width=480)
            else:
                self.text(frame,36,346,'接法位置',20,MUTED,width=350)
                for rect,key,label in [((16,372,260,424),'normal','正常'),((278,372,522,424),'A','位置 A'),((540,372,784,424),'B','位置 B')]:
                    self.button(frame,rect,label,position==key)
                self.text(frame,36,436,'外置标准件始终串联正极',20,MUTED,width=480)
                self.text(frame,36,468,'DA0=0 后仅移动 S+',20,MUTED,width=480)
            self.button(frame,(540,438,764,490),'开始测量',True)
        else:
            self.history(frame,history or {})
        notice=state.get('ui_notice')
        if notice and record is None:
            frame[:]=frame//2
            frame[176:430,80:720]=DARK
            frame[176:179,80:720]=ORANGE
            self.text(frame,112,190,notice['title'],24,ORANGE,width=576)
            self.text(frame,112,236,notice['reason'],24,TEXT,width=576)
            self.text(frame,112,286,notice['detail'],20,MUTED,width=576)
            self.text(frame,112,318,notice.get('error',''),20,MUTED,width=576)
            self.button(frame,(280,354,520,406),'知道了',True)
        return frame

    def line(self,frame,x0,y0,x1,y1,color,width=1):
        count=max(abs(int(x1)-int(x0)),abs(int(y1)-int(y0)))+1
        xs=np.rint(np.linspace(x0,x1,count)).astype(int)
        ys=np.rint(np.linspace(y0,y1,count)).astype(int)
        for dy in range(width):
            valid=(xs>=0)&(xs<800)&(ys+dy>=0)&(ys+dy<600)
            frame[ys[valid]+dy,xs[valid]]=color

    def graph(self,frame,rect,title,points,key):
        x0,y0,x1,y1=rect;frame[y0:y1,x0:x1]=SURFACE
        self.text(frame,x0+14,y0+8,title,20,MUTED,width=280)
        self.text(frame,x1-66,y0+8,'%d点'%len(points),20,MUTED,width=60)
        left,right,top,bottom=x0+68,x1-18,y0+53,y0+155
        frequencies=[p['frequency_hz'] for p in points]
        xmin=min([2.]+frequencies);xmax=max([100.]+frequencies)
        if key=='magnitude_mohm':
            high=max([1.]+[p[key]*1.12 for p in points]);power=10**np.floor(np.log10(high))
            high=next(v*power for v in (1,2,5,10) if v*power>=high);low=0.
        else:
            values=[p[key] for p in points]
            low=min(-15.,15*np.floor((min(values)-5)/15)) if values else -90.
            high=max(0.,15*np.ceil((max(values)+5)/15)) if values else 0.
        for value in (high,(high+low)/2,low):
            y=top+(high-value)/(high-low)*(bottom-top)
            self.line(frame,left,y,right,y,BORDER)
            self.text(frame,x0+5,int(y)-12,'%g'%value,20,MUTED,width=60)
        def px(f):return left+np.log(f/xmin)/np.log(xmax/xmin)*(right-left)
        ticks=sorted(set([xmin,xmax]+[f for f in (2.,10.,100.) if xmin<f<xmax]))
        for f in ticks:
            x=px(f);self.line(frame,x,top,x,bottom,BORDER)
            label='%g'%f;w=self.width(label,20)
            self.text(frame,int(max(x0+6,min(x-w/2,x1-w-6))),bottom+6,label,20,MUTED,width=70)
        self.line(frame,left,top,left,bottom,MUTED);self.line(frame,left,bottom,right,bottom,MUTED)
        self.text(frame,x0+235,bottom+6,'f/Hz',20,MUTED,width=72)
        previous=None
        for point in points:
            x=px(point['frequency_hz']);y=top+(high-point[key])/(high-low)*(bottom-top)
            color=CYAN if point['valid'] else ORANGE
            if previous and point['quality_ok'] and previous[2]['quality_ok']:
                if key!='phase_deg' or abs(point[key]-previous[2][key])<=180:
                    self.line(frame,previous[0],previous[1],x,y,color,2)
            if point['quality_ok']:
                self.line(frame,x-3,y-3,x+3,y-3,color,2);self.line(frame,x-3,y+3,x+3,y+3,color,2)
                self.line(frame,x-3,y-3,x-3,y+3,color);self.line(frame,x+3,y-3,x+3,y+3,color)
            else:
                self.line(frame,x-4,y-4,x+4,y+4,ORANGE,2);self.line(frame,x-4,y+4,x+4,y-4,ORANGE,2)
            previous=(x,y,point)
        if not points:self.text(frame,left+28,top+35,'等待测量',20,MUTED,width=200)

    def history(self,frame,history):
        selected=history.get('selected')
        if selected is not None:
            self.text(frame,20,85,'记录详情',24,width=350)
            context=selected.get('context') or {}
            temperature=context.get('temperature_c');soc=context.get('soc_fraction')
            self.text(frame,36,126,'电池 '+selected['cell_id'],24,CYAN,width=490)
            self.text(frame,548,126,'温度 '+('%.1f°C'%temperature if number(temperature) else '未知'),20,MUTED,width=216)
            self.text(frame,36,163,selected['utc'][:19]+' UTC / '+source_label(selected.get('source')),20,MUTED,width=490)
            self.text(frame,548,163,'SOC '+('%.1f%%'%(100*soc) if number(soc) and 0<=soc<=1 else '未知'),20,MUTED,width=216)
            health=health_values(selected.get('ai'))
            for rect,label,value in [((16,210,392,306),'健康状态',health['soh']),((404,210,784,306),'剩余寿命',health['rul']),
                    ((16,318,392,408),'容量',health['capacity']),((404,318,784,408),'测量状态',
                    record_status_label(selected))]:
                x,y,x1,y1=rect;frame[y:y1,x:x1]=SURFACE
                self.text(frame,x+20,y+8,label,20,MUTED,width=x1-x-40)
                self.text(frame,x+20,y+42,value,24,CYAN,width=x1-x-40)
            result=selected.get('result') or {};mag=result.get('magnitude_ohm');phase=result.get('phase_deg')
            self.text(frame,36,420,'|Z| '+('%.3f mΩ'%(mag*1000) if number(mag) else '--')+
                ' / '+('%.2f°'%phase if number(phase) else '--')+' / '+record_calibration_label(selected),20,width=728)
            self.button(frame,(16,466,220,518),'返回列表')
            self.button(frame,(548,466,784,518),'查看曲线',True)
            return
        self.text(frame,20,85,'测量记录',24,width=350)
        self.text(frame,524,87,'共 %d 条'%history.get('count',0),20,MUTED,width=250)
        for x,label in ((36,'时间 UTC'),(218,'电池'),(438,'幅值 mΩ'),(606,'SOH')):
            self.text(frame,x,120,label,20,MUTED,width=165)
        for index,row in enumerate(history.get('rows',[])[:4]):
            y=150+index*72;frame[y:y+64,16:784]=SURFACE
            utc=row['utc'];stamp=utc[5:10]+' '+utc[11:16] if len(utc)>=16 else utc
            value=(row.get('result') or {}).get('magnitude_ohm')
            self.text(frame,36,y+4,stamp,20,width=175)
            self.text(frame,218,y+4,row['cell_id'],20,CYAN,width=210)
            self.text(frame,438,y+4,'%.3f'%(value*1000) if number(value) else '--',20,width=155)
            soh=health_values(row.get('ai'))['soh']
            if row.get('prototype_mode') and soh.startswith('SOH健康度 '):soh=soh[len('SOH健康度 '):]
            self.text(frame,606,y+4,soh,20,BLUE,width=164)
            tag=record_status_label(row)+' / '+source_label(row.get('source'))
            self.text(frame,218,y+33,tag,20,MUTED,width=430)
            self.text(frame,682,y+33,'详情',20,MUTED,width=85)
        if not history.get('rows'):self.text(frame,280,260,'暂无测量记录',24,MUTED,width=350)
        self.button(frame,(16,466,194,518),'上一页')
        self.text(frame,300,476,'%d / %d 页'%(history.get('page',0)+1,history.get('pages',1)),24,width=245)
        self.button(frame,(606,466,784,518),'下一页')

def action(point,page,busy,record_detail=False,viewing_record=False,notice=False):
    x,y=point
    if notice:return ('dismiss_notice',None) if 280<=x<520 and 354<=y<406 else None
    for index,(x0,y0,x1,y1) in enumerate(NAV_RECTS):
        if x0<=x<x1 and y0<=y<y1:return ('page',index)
    if 646<=x<784 and 10<=y<60 and busy:return ('cancel',None)
    if page==2:
        if record_detail:
            if 16<=x<220 and 466<=y<518:return ('record_back',None)
            if 548<=x<784 and 466<=y<518:return ('record_curve',None)
        else:
            if 16<=x<194 and 466<=y<518:return ('record_page',-1)
            if 606<=x<784 and 466<=y<518:return ('record_page',1)
            if 16<=x<784 and 150<=y<430 and (y-150)%72<64:return ('record_open',(y-150)//72)
        return None
    if page==0 and viewing_record:
        return ('return_live',None) if 548<=x<764 and 458<=y<514 else None
    if busy:return ('cancel',None) if 646<=x<784 and 10<=y<60 else None
    if page==0 and 548<=x<764 and 458<=y<514:return ('start',None)
    if page==1:
        if 540<=x<764 and 438<=y<490:return ('start',None)
        if 420<=x<578 and 142<=y<202:return ('frequency',-1)
        if 600<=x<764 and 142<=y<202:return ('frequency',1)
        if 284<=y<336:return ('sweep',x>=404)
        if 372<=y<424:return ('position','normal' if x<260 else 'A' if x<522 else 'B')
    return None
