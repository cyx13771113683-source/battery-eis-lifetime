"""Explicit host simulation backend. Never imported by the hardware path."""
import time
from pathlib import Path
import numpy as np
from configuration import ABI,phase_increment,RAW_CAPACITY
from hardware import Capture
from analysis import moments_from_raw

def sine_lut():
    values=np.loadtxt(Path(__file__).resolve().parents[1]/'rtl/sine14.mem',dtype=str)
    lut=np.array([int(v,16) for v in values],dtype=np.int64)
    return np.where(lut>=8192,lut-16384,lut)

def make_capture(settings,profile,sequence=1,drive=True,noise_v=1e-6):
    settings.validate()
    rng=np.random.default_rng(342+sequence)
    ticks=np.arange(round(settings.settle_s*1e8)+21000,round((settings.settle_s+settings.capture_s)*1e8),32768,dtype=np.int64)
    channels=np.arange(len(ticks))%2
    phase=(ticks*phase_increment(settings.frequency_hz))%(2**32)
    radians=phase*2*np.pi/2**32
    voltage=np.full(len(ticks),3.7);current=np.zeros(len(ticks))
    if drive:
        # Nominal load + measured-shunt setting + assumed MOS + model DC R.
        # This affects this explicit simulation only; hardware I is measured.
        high=3.7/(profile.r_load_nominal_ohm+profile.r_shunt_ohm+.035+.04);current[:]=high/2
        voltage-=high/2*.04
        for harmonic in (1,3,5):
            i=-1j*2*high/(np.pi*harmonic)
            z=.01+.03/(1+1j*2*np.pi*settings.actual_frequency_hz*harmonic*.01)
            current+=np.real(i*np.exp(1j*harmonic*radians))
            voltage-=np.real(z*i*np.exp(1j*harmonic*radians))
    adc=np.where(channels==0,voltage/profile.voltage_divider,current*profile.r_shunt_ohm)
    code=np.round((adc+rng.normal(0,noise_v,len(adc)))/profile.code_volts).astype(np.int64)
    raw=np.column_stack((ticks,phase,code,channels))
    if len(raw)>RAW_CAPACITY:raise ValueError('synthetic capture exceeds BE03 raw capacity')
    return Capture(raw,moments_from_raw(raw,sine_lut()),dict(sequence=sequence,status=(ABI<<16)|(sequence<<8)|21,
                  faults=0,source='simulation',simulation_model='R0=10mOhm,Rp=30mOhm,tau=10ms; 1/3/5 harmonics',
                  configuration=[phase_increment(settings.frequency_hz),round(settings.settle_s*1e8),round(settings.capture_s*1e8),settings.gate_high_code],
                  adc_registers=[0xf0000130,0,0x400000,32768]))

class SyntheticHardware:
    def __init__(self):self.capture=None;self.sequence=0;self.active=False;self.ends=0
    def initialize(self):return dict(config=0xf0000130,ofc=0,fsc=0x400000,slot_cycles=32768,source='simulation')
    def start(self,settings,profile,drive=True):
        self.sequence+=1;self.active=True
        self.capture=make_capture(settings,profile,self.sequence,drive)
        self.ends=time.monotonic()+.04
    def stop(self,timeout=.1):
        if self.active and time.monotonic()<self.ends:self.capture.meta['faults']=0x200
        self.active=False
    def poll(self):
        if time.monotonic()>=self.ends:self.active=False
        return (ABI<<16)|(self.sequence<<8)|(3 if self.active else 21)
    def read_capture(self,deadline=None):return self.capture
