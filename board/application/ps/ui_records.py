"""Read-only, lazy browsing of existing session JSON; no database or hardware."""
import json
import math
import os
from collections import OrderedDict
from pathlib import Path

UI_LABELS = '寿命分析剩余容量估计研究模型待验证实测仿真回放来源未知测量记录共条页上下详情返回列表查看曲线历史实时等待幅值频率无有效频点时间电池编号温度读数失败未标定已标定待核验记录损坏天次'
UI_LABELS += '未能开始测量测量条件尚未核验请先完成接线与校准核验本次测量已停止原因见首页状态行零激励电流检查未通过排查知道了'
UI_LABELS += '寿命结果健康度电脑端维护校正已载入'


def start_notice(error):
    """Explain why a start request was refused; the raw reason stays visible."""
    error = str(error or '')
    if 'physical module' in error:
        reason, detail = '测量条件尚未核验', '请先完成接线与校准核验'
    elif 'A/B requires' in error:
        reason, detail = 'A/B 校准条件未确认', '请先确认校准接法与编号'
    elif 'current present with DA0 zero' in error:
        reason, detail = '零激励电流检查未通过', '请先完成零点排查'
    else:
        reason, detail = '本次测量已停止', '原因见首页状态行'
    return dict(title='未能开始测量', reason=reason, detail=detail, error=error)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def source_label(source):
    return {'hardware': '实测', 'simulation': '仿真', 'public_dataset_replay': '回放'}.get(source, '来源未知')


def record_status_label(record):
    if record.get('prototype_mode'):
        status=record.get('status')
        if status=='DONE':
            rows=record.get('spectrum') or []
            return '测量完成' if rows and record.get('ratio_corrected') and all(r.get('quality_ok') for r in rows) else '待核验'
        return {'ERROR':'测量错误','TIMEOUT':'采集超时','CANCELLED':'已取消'}.get(status,'待核验')
    return '记录有效' if record.get('measurement_valid') else '待核验'


def record_calibration_label(record):
    if record.get('prototype_mode'):return '已校正' if record.get('ratio_corrected') else '未校正'
    return '已标定' if record.get('calibrated') else '未标定'


def health_values(ai):
    """Numbers remain pending unless their model status actually permits them."""
    ai = ai if isinstance(ai, dict) else {}
    research = ai.get('status') == 'research_estimate'
    prototype = ai.get('status') == 'prototype_result'
    accepted = prototype or research or (ai.get('status') == 'validated_estimate' and ai.get('valid_for_board') is True)
    soh = ai.get('soh')
    soh_text = ('SOH健康度 ' if prototype else '研究 SOH ' if research else 'SOH ') + ('%.1f%%' % (soh*100)) if accepted and number(soh) and 0 <= soh <= 1 else 'SOH --' if ai.get('status')=='no_result' else 'SOH 待核验'
    capacity = ai.get('capacity_Ah')
    capacity_text = ('估计 ' if research else '') + '%.3f Ah' % capacity if accepted and number(capacity) and capacity > 0 else '容量 待核验'
    rul, unit = ai.get('rul'), ai.get('rul_unit')
    units = {'cycles': '次', 'days': '天'}
    rul_text = ('研究 ' if research else '') + '%.0f %s' % (rul, units[unit]) if accepted and number(rul) and rul >= 0 and unit in units else 'RUL 待核验'
    if prototype:
        if capacity is None:capacity_text='容量 --'
        if rul is None:rul_text='剩余寿命 --'
    return dict(soh=soh_text, capacity=capacity_text, rul=rul_text)


def has_soh(ai):
    """Only a permitted, finite SOH percentage belongs in operator history."""
    if not isinstance(ai,dict):return False
    accepted=ai.get('status') in ('prototype_result','research_estimate') or (
        ai.get('status')=='validated_estimate' and ai.get('valid_for_board') is True)
    return accepted and number(ai.get('soh')) and 0<=ai['soh']<=1


def spectrum_points(results):
    """Average repeated complex measurements, never average wrapped phases."""
    groups = {}
    for row in results or ():
        if not isinstance(row, dict): continue
        frequency, z = row.get('frequency_hz'), row.get('impedance_ohm')
        if not number(frequency) or frequency <= 0 or not isinstance(z, (list, tuple)) or len(z) != 2 or not all(number(x) for x in z):
            continue
        groups.setdefault(float(frequency), []).append((complex(*z), row))
    points = []
    for frequency, rows in sorted(groups.items()):
        good = [(z, row) for z, row in rows if row.get('quality_ok') is True]
        selected = good or rows
        mean = sum(z for z, _ in selected) / len(selected)
        if not number(abs(mean)*1000): continue
        points.append(dict(frequency_hz=frequency, magnitude_mohm=abs(mean)*1000,
            phase_deg=math.degrees(math.atan2(mean.imag, mean.real)),
            quality_ok=bool(good) and len(good) == len(rows),
            valid=all(row.get('valid') is True for _, row in rows), repeats=len(rows)))
    return points


class RecordIndex:
    """Index names first; decode JSON only for requested rows, with mtime cache."""
    def __init__(self, root, page_size=4, soh_only=False, current_run_only=False):
        self.root = Path(root)
        self.page_size = page_size
        if type(page_size) is not int or page_size < 1: raise ValueError('positive page size required')
        self.entries = []; self.cache = OrderedDict();self.soh_only=soh_only;self._eligibility={}
        # Snapshot directories, not timestamps: the board has no reliable RTC.
        # Even unfinished sessions from the previous run must remain hidden.
        self._before_start={p.name for p in self.root.iterdir() if p.is_dir()} if current_run_only and self.root.is_dir() else set()
        self.refresh()

    def refresh(self):
        entries = []
        if self.root.is_dir():
            with os.scandir(self.root) as folders:
                for folder in folders:
                    if folder.is_dir(follow_symlinks=False):
                        path = Path(folder.path)
                        if path.name in self._before_start:continue
                        if (path / 'session.json').is_file() and (path / 'summary.json').is_file():
                            if not self.soh_only or self._has_soh(path):entries.append(path)
        self.entries = sorted(entries, key=lambda p: p.name, reverse=True)
        active = set(self.entries)
        self.cache = OrderedDict((path, value) for path, value in self.cache.items() if path in active)
        self._eligibility={path:value for path,value in self._eligibility.items() if path.is_dir()}

    def _has_soh(self,path):
        try:
            stats=[(path/name).stat() for name in ('session.json','summary.json')]
            stamp=tuple((stat.st_mtime_ns,stat.st_size) for stat in stats)
            previous=self._eligibility.get(path)
            if previous is not None and previous[0]==stamp:return previous[1]
            eligible=has_soh(self.load(path).get('ai'))
            self._eligibility[path]=(stamp,eligible)
            return eligible
        except (OSError,ValueError,TypeError):return False

    @property
    def page_count(self):
        return max(1, (len(self.entries)+self.page_size-1)//self.page_size)

    def clamp(self, page):
        return max(0, min(int(page), self.page_count-1))

    def page(self, page):
        offset = self.clamp(page)*self.page_size
        return [self.load(path) for path in self.entries[offset:offset+self.page_size]]

    def load(self, path):
        path = Path(path)
        try:
            stamp = tuple((path / name).stat().st_mtime_ns for name in ('session.json', 'summary.json'))
            if path in self.cache and self.cache[path][0] == stamp:
                self.cache.move_to_end(path)
                return self.cache[path][1]
            session = json.loads((path / 'session.json').read_text(encoding='utf-8-sig'))
            summary = json.loads((path / 'summary.json').read_text(encoding='utf-8-sig'))
            if not isinstance(session, dict) or not isinstance(summary, dict): raise ValueError('not a session object')
            rows = summary.get('results', [])
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows): raise ValueError('malformed result rows')
            ai = summary.get('ai') if isinstance(summary.get('ai'), dict) else {}
            context = session.get('context') if isinstance(session.get('context'), dict) else {}
            profile = session.get('profile') if isinstance(session.get('profile'), dict) else {}
            result = rows[-1] if rows else None
            record = dict(id=path.name, session_dir=str(path), utc=str(session.get('utc_started', '未知')),
                cell_id=str(profile.get('cell_id', '未知')), context=context, source=summary.get('source'),
                status=summary.get('status', 'ERROR'), ai=ai, result=result, spectrum=rows,
                prototype_mode=session.get('prototype_mode') is True,
                ratio_corrected=bool(rows) and all(row.get('prototype_calibration_id') for row in rows),
                frequency_hz=result['frequency_hz'] if result and number(result.get('frequency_hz')) and result['frequency_hz']>0 else 80.,
                calibrated=bool(rows) and all(row.get('calibration_id') for row in rows),
                measurement_valid=summary.get('status') == 'DONE' and bool(rows) and all(
                    row.get('valid') is True and row.get('quality_ok') is True for row in rows),
                error=summary.get('error'), read_error=False)
            self.cache[path] = (stamp, record)
            while len(self.cache)>32:self.cache.popitem(last=False)
            return record
        except (OSError, ValueError, TypeError, KeyError):
            return dict(id=path.name, session_dir=str(path), utc='未知', cell_id='记录损坏',
                context={}, source=None, status='ERROR', ai={}, result=None, spectrum=[],
                frequency_hz=80., calibrated=False, measurement_valid=False, error='记录读取失败', read_error=True)


class RecordBrowser:
    def __init__(self, root, current_run_only=False):
        self.index = RecordIndex(root,soh_only=True,current_run_only=current_run_only)
        self.page = 0; self.selected = None; self.chart_record = None
        self._view = None

    def refresh(self):
        self.index.refresh(); self.page = self.index.clamp(self.page); self._view = None

    def move(self, delta):
        self.page = self.index.clamp(self.page+delta); self.selected = None; self._view = None

    def open(self, row):
        rows = self.index.page(self.page)
        if 0 <= row < len(rows): self.selected = rows[row]
        self._view = None

    def back(self):
        self.selected = None; self._view = None

    def view(self):
        if self._view is None:
            self._view = dict(page=self.page, pages=self.index.page_count, count=len(self.index.entries),
                rows=self.index.page(self.page), selected=self.selected)
        return self._view
