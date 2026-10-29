"""BE03 GPIO mailbox driver. Hardware import is delayed for host-only tests."""
from dataclasses import dataclass
import time
import hashlib
import json
from pathlib import Path
import numpy as np
from configuration import ABI, CLOCK_HZ, phase_increment, RAW_CAPACITY, MAX_CAPTURE_S

RUN=1<<31
DRIVE=1<<30
INIT=1<<29
HEARTBEAT=1<<28
TOGGLE=1<<27
MOMENTS=1<<22

@dataclass
class Capture:
    raw: np.ndarray
    moments: tuple
    meta: dict

def signed64(lo, hi):
    x=int(lo)|(int(hi)<<32)
    return x-(1<<64) if x&(1<<63) else x

class BatteryHardware:
    def __init__(self, overlay):
        self.config=overlay.waveshare_config.mmio
        self.status=overlay.waveshare_status.mmio
        self.left=overlay.left_result.mmio
        self.right=overlay.right_result.mmio
        self.control=0
        self.last_heartbeat=0
        self.baseline_sequence=None
        self._check_abi(self.read_status())
        self.stop()

    @staticmethod
    def load(path, profile):
        profile.validate(arm=True)
        path=Path(path)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=profile.overlay_sha256:
            raise ValueError('overlay hash does not match confirmed hardware profile')
        if not path.with_suffix('.hwh').is_file():
            raise FileNotFoundError('matching HWH required')
        manifest=json.loads((path.parent/'manifest.json').read_text(encoding='utf-8-sig'))
        if (manifest['abi']!=ABI or manifest['frontend_sha256']!=profile.frontend_sha256
                or manifest.get('assembly_sha256')!=profile.assembly_sha256):
            raise ValueError('release hardware binding mismatch')
        for artifact in (path,path.with_suffix('.hwh')):
            if hashlib.sha256(artifact.read_bytes()).hexdigest()!=manifest['files'][artifact.name]:
                raise ValueError('BIT/HWH release pair hash mismatch')
        from pynq import Overlay
        overlay=Overlay(str(path))
        return BatteryHardware(overlay), overlay

    def read_status(self):
        value=self.status.read(0)
        self._check_abi(value)
        return value

    @staticmethod
    def _check_abi(status):
        if status>>16 != ABI:
            raise RuntimeError('requires battery overlay ABI BE03; BE01/BE02 and old RLC ABI refused')

    def _write(self):
        self.config.write(8, self.control)

    def stop(self, timeout=.1):
        self.control &= ~(RUN|DRIVE)
        self._write()
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            status=self.read_status()
            if status&16 and not status&2:
                return
            time.sleep(.001)
        raise TimeoutError('DA0 zero acknowledgement absent; disconnect battery before touching wiring')

    def initialize(self, timeout=3.):
        self.stop()
        self.control|=INIT;self._write();time.sleep(.001)
        self.control&=~INIT;self._write()
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            status=self.read_status()
            if status&8: raise RuntimeError('ADC initialization failed')
            if status&1 and status&16:
                registers=self.page(10)
                if registers[0]&0xfffffffe != 0xf0000130:
                    raise RuntimeError('ADS ID/configuration readback mismatch')
                if tuple(self.page(12)) != (RAW_CAPACITY, phase_increment(2), phase_increment(100), round(MAX_CAPTURE_S*CLOCK_HZ)):
                    raise RuntimeError('BE03 capture capability readback mismatch')
                return {'config':registers[0],'ofc':registers[1],'fsc':registers[2], 'slot_cycles':registers[3]}
            time.sleep(.005)
        self.stop()
        raise TimeoutError('ADS self calibration timeout')

    def command(self, op, value):
        status=self.read_status()
        if status&2 or not status&16:
            raise RuntimeError('configuration requires stopped, DA0 zero')
        self.config.write(0, int(value))
        self.control=(self.control&~(15<<23))|(op<<23)
        self.control ^= TOGGLE
        self._write()
        if bool(self.read_status()&128)!=bool(self.control&TOGGLE):
            raise RuntimeError('mailbox acknowledgement failed')

    def start(self, settings, profile, drive=True):
        settings.validate();profile.validate(arm=True)
        self.stop()
        for op,value in [(1,phase_increment(settings.frequency_hz)),(2,round(settings.settle_s*CLOCK_HZ)),
                         (3,round(settings.capture_s*CLOCK_HZ)),*list(zip((4,5,6),profile.trip_codes())),
                         (7,settings.gate_high_code)]:
            self.command(op,value)
        self.baseline_sequence=(self.read_status()>>8)&255
        self.control|=RUN
        if drive:self.control|=DRIVE
        else:self.control&=~DRIVE
        self._write();self.heartbeat(force=True)
        status=self.read_status()
        if ((status>>8)&255)==self.baseline_sequence:
            raise RuntimeError('capture start refused or stale acknowledgement')
        self.expected_sequence=(status>>8)&255

    def heartbeat(self, force=False):
        now=time.monotonic()
        if force or now-self.last_heartbeat>=.08:
            self.control^=HEARTBEAT;self._write();self.last_heartbeat=now

    def poll(self):
        status=self.read_status()
        if status&2:self.heartbeat()
        return status

    def page(self, index):
        self.control=(self.control & ~((63<<16)|(RAW_CAPACITY-1)))|MOMENTS|(index<<16)
        self._write()
        return self.left.read(0),self.left.read(8),self.right.read(0),self.right.read(8)

    def read_capture(self, deadline=None):
        status=self.read_status()
        if status&2 or not status&4 or not status&16:
            raise RuntimeError('capture is not frozen and zero acknowledged')
        sequence=(status>>8)&255
        if sequence!=self.expected_sequence:
            raise RuntimeError('capture sequence changed')
        count,ticks,faults,seq=self.page(9)
        if not 0<=count<=RAW_CAPACITY or seq!=sequence:
            raise RuntimeError('invalid frozen capture metadata')
        adc=self.page(10);cfg=self.page(11)
        moments=[[],[]]
        for j in range(9):
            a,b,c,d=self.page(j)
            moments[0].append(signed64(a,b));moments[1].append(signed64(c,d))
        raw=np.empty((count,4),dtype=np.int64)
        self.control&=~(MOMENTS|(RAW_CAPACITY-1))
        for j in range(count):
            if j%128==0 and deadline is not None and time.monotonic()>=deadline:
                raise TimeoutError('session deadline exceeded during frozen RAM read')
            self.config.write(8,self.control|j)
            # AXI read roundtrip exceeds the RAM's one-cycle pipeline latency.
            t=self.left.read(0);p=self.left.read(8);code=self.right.read(0);ch=self.right.read(8)
            raw[j]=[t,p,code-(1<<32) if code&(1<<31) else code,ch]
        if self.read_status()!=status:
            # Page bits have no effect on status; frozen status must not change.
            raise RuntimeError('capture changed while reading')
        return Capture(raw,tuple(moments),dict(sequence=sequence,status=status,faults=faults,
                       ticks=ticks,adc_registers=adc,configuration=cfg,source='hardware',
                       timestamp_convention='conversion-ready DRDY; filter aperture not directly measured'))
