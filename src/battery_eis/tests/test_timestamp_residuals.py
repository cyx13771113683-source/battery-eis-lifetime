"""Residual serial-correlation diagnostics; synthetic records only."""
from pathlib import Path
import sys
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ps'))
from analysis import diagnose
from configuration import phase_increment

class TimestampResidualTests(unittest.TestCase):
    def record(self,colored=False):
        tick=np.arange(2400,dtype=np.int64)*65536
        phase=(tick*phase_increment(80))%(2**32)
        angle=phase*(2*np.pi/2**32)
        rng=np.random.default_rng(20261028)
        noise=rng.normal(0,3,len(tick))
        if colored:
            for k in range(1,len(noise)):noise[k]+=.97*noise[k-1]
        code=np.rint(4000000+2000*np.cos(angle)+500*np.sin(angle)+noise).astype(np.int64)
        return np.column_stack((tick,phase,code,np.zeros(len(tick),dtype=np.int64)))

    def test_correlated_noise_is_visible(self):
        result=diagnose(self.record(colored=True),0,80)
        self.assertGreater(result['residual_lag1_correlation'],.7)

    def test_iid_residuals_have_small_lag_one(self):
        result=diagnose(self.record(),0,80)
        self.assertLess(abs(result['residual_lag1_correlation']),.1)

    def test_new_field_is_not_a_validity_or_calibration_claim(self):
        result=diagnose(self.record(colored=True),0,80)
        self.assertNotIn('valid',result)
        self.assertNotIn('board_validated',result)
        self.assertNotIn('calibration_id',result)
        self.assertIn('phasor',result)

if __name__=='__main__':unittest.main()
