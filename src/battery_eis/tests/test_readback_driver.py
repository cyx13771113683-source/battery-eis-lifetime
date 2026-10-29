"""Exercise real driver code against a frozen AXI register model, without PYNQ."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ps'))
from hardware import BatteryHardware,MOMENTS
from configuration import ABI,RAW_CAPACITY


class FrozenAXI:
    def __init__(self,count=257):
        self.raw=np.array([[100+i*32768,(100+i*32768)*86,(-1 if i%2 else 1)*(i+1),i%2]
                          for i in range(count)],dtype=np.int64)
        self.moments=((count//2+1,2,-3,4,-5,6,-7,8,-9),(count//2,-20,30,-40,50,-60,70,-80,90))
        self.sequence=255;self.status=(ABI<<16)|(self.sequence<<8)|0x15
        self.control=0;self.count=count;self.faults=0
        self.reads=0;self.writes=0;self.status_reads=0;self.change_on_final_status=False
        self.pages={9:(count,123456,0,self.sequence),10:(0xf0000130,0,0x400000,32768),
                    11:(86,20000000,400000000,65535)}
        self.overlay=SimpleNamespace(**{name:SimpleNamespace(mmio=self.Port(self,name)) for name in
            ('waveshare_config','waveshare_status','left_result','right_result')})

    class Port:
        def __init__(self,parent,name):self.parent=parent;self.name=name
        def read(self,offset):
            p=self.parent;p.reads+=1
            if self.name=='waveshare_status':
                p.status_reads+=1
                return p.status^(0x100 if p.change_on_final_status and p.status_reads>=2 else 0)
            column=(0 if self.name=='left_result' else 2)+(1 if offset==8 else 0)
            if p.control&MOMENTS:
                page=(p.control>>16)&63
                if page<9:
                    value=p.moments[column//2][page]&((1<<64)-1)
                    return (value>>(32*(column%2)))&0xffffffff
                return p.pages[page][column]
            index=p.control&(RAW_CAPACITY-1)
            return int(p.raw[index,column])&0xffffffff
        def write(self,offset,value):
            if self.name!='waveshare_config' or offset!=8:raise AssertionError('unexpected AXI write')
            self.parent.writes+=1;self.parent.control=int(value)

    def driver(self):
        driver=BatteryHardware(self.overlay);driver.expected_sequence=self.sequence
        self.reads=self.writes=self.status_reads=0
        return driver


class ReadbackDriverTests(unittest.TestCase):
    def test_exact_signed_raw_and_64bit_moments_and_operation_count(self):
        axi=FrozenAXI();capture=axi.driver().read_capture()
        np.testing.assert_array_equal(capture.raw,axi.raw)
        self.assertEqual(capture.moments,tuple(list(x) for x in axi.moments))
        self.assertEqual(capture.meta['sequence'],255)
        self.assertEqual(axi.writes,len(axi.raw)+12)
        self.assertEqual(axi.reads,4*len(axi.raw)+50)

    def test_stale_sequence_is_refused_before_ram_reads(self):
        axi=FrozenAXI();driver=axi.driver();driver.expected_sequence=254
        with self.assertRaisesRegex(RuntimeError,'sequence changed'):driver.read_capture()
        self.assertEqual(axi.writes,0)

    def test_over_capacity_header_rejected_before_data_addressing(self):
        axi=FrozenAXI();driver=axi.driver()
        axi.pages[9]=(RAW_CAPACITY+1,0,0,255)
        with self.assertRaisesRegex(RuntimeError,'invalid frozen'):driver.read_capture()
        self.assertEqual(axi.writes,1)

    def test_frozen_status_change_rejected_after_read(self):
        axi=FrozenAXI();driver=axi.driver();axi.change_on_final_status=True
        with self.assertRaisesRegex(RuntimeError,'changed while reading'):driver.read_capture()

    def test_deadline_aborts_after_first_128_samples(self):
        axi=FrozenAXI();driver=axi.driver()
        with patch('hardware.time.monotonic',side_effect=[0.,2.]):
            with self.assertRaisesRegex(TimeoutError,'deadline exceeded'):driver.read_capture(deadline=1.)
        self.assertEqual(axi.writes,12+128)
        self.assertEqual(axi.reads,49+4*128)

    def test_active_or_unacknowledged_stop_cannot_be_read(self):
        for status in (0x17,0x05):
            axi=FrozenAXI();driver=axi.driver();axi.status=(ABI<<16)|(255<<8)|status
            with self.assertRaisesRegex(RuntimeError,'not frozen'):driver.read_capture()
            self.assertEqual(axi.writes,0)


if __name__=='__main__':unittest.main(verbosity=2)
