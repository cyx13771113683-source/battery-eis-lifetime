"""Explicit hardware identity and battery-specific limits; no implicit arming."""
from dataclasses import dataclass, asdict, replace
import hashlib
import json
import math
from pathlib import Path

ABI = 0xBE03
ASSEMBLY_ID = 'rev-a-final-20261021-r1-10-r2-r100-r5-2490'
ASSEMBLY_SHA256 = 'f53cbee8d08a3656c9d681e1cc31ad869669dd1c514bddc3d7e2ccd7fbe0adb3'
MAX_VOLTAGE_TRIP_CODE = 8388606
MAX_CURRENT_TRIP_CODE = 120000
CLOCK_HZ = 100_000_000
DEFAULT_FREQUENCY = 80.0
DEFAULT_SWEEP = (2.16, 17.8)
SCHEMA = 'battery-eis-rev-a/3'
RAW_CAPACITY = 16384
MIN_FREQUENCY = 2.0
MAX_FREQUENCY = 100.0
MAX_CAPTURE_S = 4.0

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def finite(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('%s must be finite in [%s, %s]' % (name, low, high))
    return float(value)

def phase_increment(frequency):
    return round(finite(frequency, MIN_FREQUENCY, MAX_FREQUENCY, 'frequency_hz') * 2**32 / CLOCK_HZ)

@dataclass(frozen=True)
class Settings:
    frequency_hz: float = DEFAULT_FREQUENCY
    capture_s: float = 1.0
    settle_s: float = 0.25
    repeats: int = 2
    gate_high_code: int = 65535

    def validate(self):
        phase_increment(self.frequency_hz)
        finite(self.capture_s, .2, MAX_CAPTURE_S, 'capture_s')
        finite(self.settle_s, .2, 1., 'settle_s')
        if self.capture_s * self.actual_frequency_hz < 8:
            raise ValueError('at least eight cycles per capture required')
        if type(self.repeats) is not int or not 2 <= self.repeats <= 20:
            raise ValueError('repeats must be an integer in 2..20')
        if type(self.gate_high_code) is not int or not 60000 <= self.gate_high_code <= 65535:
            raise ValueError('gate_high_code must be 60000..65535')
        return self

    def for_frequency(self, frequency):
        """At least eight actual DDS cycles; low frequencies settle for one cycle.

        This is a candidate time allocation, not proof of electrochemical settling.
        Integer ceiling prevents a rounded window from being shorter than 8 cycles.
        """
        inc = phase_increment(frequency)
        capture_ticks = max(round(self.capture_s * CLOCK_HZ), (8 * 2**32 + inc - 1) // inc)
        settle_ticks = round(self.settle_s * CLOCK_HZ)
        if frequency < 10:
            settle_ticks = max(settle_ticks, (2**32 + inc - 1) // inc)
        return replace(self, frequency_hz=frequency, capture_s=capture_ticks / CLOCK_HZ,
                       settle_s=settle_ticks / CLOCK_HZ).validate()

    @property
    def actual_frequency_hz(self):
        return phase_increment(self.frequency_hz) * CLOCK_HZ / 2**32

@dataclass(frozen=True)
class HardwareProfile:
    hardware_id: str
    board_serial: str
    module_serial: str
    wiring_verified: bool
    module_verified: bool
    adc_clock_verified: bool
    polarity_verified: bool
    limits_confirmed: bool
    chemistry: str
    cell_id: str
    minimum_voltage_v: float
    maximum_voltage_v: float
    maximum_current_a: float
    frontend_sha256: str
    overlay_sha256: str
    assembly_sha256: str
    adc_clock_hz: int = 7680000
    adc_reference_v: float = 2.5
    dac_reference_v: float = 5.0
    r_shunt_ohm: float = 0.100
    voltage_divider: float = 1.249
    r_load_nominal_ohm: float = 10.0
    voltage_dc_scale: float = 1.0
    current_dc_scale: float = 1.0
    dc_reference_source: str = 'nominal only'

    @classmethod
    def load(cls, path):
        return cls(**json.loads(Path(path).read_text(encoding='utf-8-sig')))

    def validate(self, arm=False):
        if not all(isinstance(s, str) and s.strip() for s in (self.hardware_id, self.board_serial, self.module_serial, self.chemistry, self.cell_id)):
            raise ValueError('hardware/cell identity and chemistry are required')
        finite(self.minimum_voltage_v, .1, 4.9, 'minimum_voltage_v')
        finite(self.maximum_voltage_v, self.minimum_voltage_v+.05, 5., 'maximum_voltage_v')
        finite(self.maximum_current_a, .001, .55, 'maximum_current_a')
        finite(self.r_shunt_ohm, .098, .102, 'r_shunt_ohm')
        finite(self.voltage_divider, 1.24, 1.26, 'voltage_divider')
        finite(self.r_load_nominal_ohm, 10., 10., 'r_load_nominal_ohm')
        finite(self.voltage_dc_scale, .8, 1.2, 'voltage_dc_scale')
        finite(self.current_dc_scale, .8, 1.2, 'current_dc_scale')
        if (self.adc_clock_hz, self.adc_reference_v, self.dac_reference_v) != (7680000, 2.5, 5.0):
            raise ValueError('BE03 requires 7.68MHz ADS clock, ADC 2.5V and DAC 5V references')
        for value in (self.frontend_sha256, self.overlay_sha256, self.assembly_sha256):
            if len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('SHA256 identity required')
        if self.assembly_sha256 != ASSEMBLY_SHA256:
            raise ValueError('profile must match final 10ohm/R100/1.249 assembly authority')
        self.trip_codes()  # Reject unrepresentable limits rather than silently clipping.
        if arm and not all(x is True for x in (self.wiring_verified, self.module_verified, self.adc_clock_verified, self.polarity_verified, self.limits_confirmed)):
            raise ValueError('physical module, wiring, clock, polarity and chemistry limits require confirmation in profile')
        return self

    def binding(self, settings):
        keys=('hardware_id','board_serial','module_serial','frontend_sha256','overlay_sha256','assembly_sha256',
              'adc_clock_hz','adc_reference_v','dac_reference_v','r_shunt_ohm','voltage_divider',
              'voltage_dc_scale','current_dc_scale','dc_reference_source','r_load_nominal_ohm')
        values=asdict(self)
        return dict({k: values[k] for k in keys}, schema=SCHEMA, abi=ABI, assembly_id=ASSEMBLY_ID, drate=0xf0,
                    pga=1, buffer=False, mux=[1,35], slot_cycles=32768,
                    timestamp='DRDY-ready-100MHz', waveform='unipolar-square-50pct',
                    gate_high_code=settings.gate_high_code,
                    phase_increment=phase_increment(settings.frequency_hz),
                    raw_capacity_samples=RAW_CAPACITY, capture_ticks=round(settings.capture_s*CLOCK_HZ),
                    settle_ticks=round(settings.settle_s*CLOCK_HZ), estimator='dc-sin-cos-moments-v1')

    @property
    def code_volts(self):
        return 2*self.adc_reference_v / 8388607

    def trip_codes(self):
        v_per_code=self.code_volts*self.voltage_divider*self.voltage_dc_scale
        i_per_code=self.code_volts/self.r_shunt_ohm*self.current_dc_scale
        codes=(round(self.minimum_voltage_v/v_per_code),round(self.maximum_voltage_v/v_per_code),
               round(self.maximum_current_a/i_per_code))
        if not (0<codes[0]<codes[1]<=MAX_VOLTAGE_TRIP_CODE and 0<codes[2]<=MAX_CURRENT_TRIP_CODE):
            raise ValueError('configured protection limits exceed BE03 ADC/PL code range')
        return codes
