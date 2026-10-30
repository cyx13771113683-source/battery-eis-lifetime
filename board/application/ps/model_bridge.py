"""Connect calibrated complex spectra to frozen research models without domain relabeling."""
import hashlib
import json
import math
from pathlib import Path
from research_inference import predict


def no_model():
    return dict(status='no_validated_battery_model', reason='model_not_configured',
                soh=None, capacity_Ah=None, rul=None, valid_for_board=False)


class HealthModel:
    def __init__(self, path):
        self.path = Path(path)
        data = self.path.read_bytes()
        self.sha256 = hashlib.sha256(data).hexdigest()
        self.model = json.loads(data)
        if self.model.get('schema') != 'battery-eis-research-model/1' or self.model.get('valid_for_board') is not False:
            raise ValueError('only frozen, explicitly unvalidated research models are supported')
        if self.model['estimator']['kind'] not in ('ridge', 'forest'):
            raise ValueError('unsupported research model kind')
        fs = self.model['frequencies_hz']
        if len(fs) != 2 or not all(isinstance(f, (int, float)) and math.isfinite(f) and f > 0 for f in fs):
            raise ValueError('two finite model frequencies required')

    def _annotate(self, result, sample=None):
        return dict(result, model_file=self.path.name, model_sha256=self.sha256,
                    backend='PS portable research inference', input=sample)

    def reject(self, reason, sample=None):
        return self._annotate(dict(status='rejected', reason=reason, soh=None,
                                  capacity_Ah=None, rul=None, valid_for_board=False), sample)

    def public_replay(self, sample):
        """Explicit public-data replay only. Never called by a measurement session."""
        result = self._annotate(predict(self.model, sample), sample)
        result['source'] = 'public_dataset_replay'
        return result

    def evaluate(self, results, context, chemistry, source, terminal, position='normal'):
        if terminal not in ('DONE', 'UNVERIFIED'):
            return self.reject('session_incomplete')
        if position != 'normal' or context.get('port_kind', 'battery_terminal') != 'battery_terminal':
            return self.reject('not_battery_terminal_measurement')
        if not results or not all(r.get('valid') is True and r.get('quality_ok') is True
                                  and r.get('calibration_id') for r in results):
            return self.reject('measurement_or_calibration_invalid')
        groups = [[] for _ in self.model['frequencies_hz']]
        for row in results:
            if row.get('source') != source:
                return self.reject('measurement_source_mismatch')
            f = row.get('frequency_hz')
            if not isinstance(f, (int, float)) or not math.isfinite(f):
                return self.reject('frequency_mismatch')
            indices = [i for i, target in enumerate(self.model['frequencies_hz'])
                       if abs(f - target) <= max(.002, target * .0001)]
            if len(indices) != 1:
                return self.reject('frequency_mismatch')
            groups[indices[0]].append(row)
        spectra = []
        for rows in groups:
            if len(rows) < 2:
                return self.reject('missing_independent_repeats')
            if len({r.get('repeat') for r in rows}) != len(rows):
                return self.reject('duplicate_repeat')
            if any(r.get('binding') != rows[0].get('binding') or
                   r['calibration_id'] != rows[0]['calibration_id'] for r in rows):
                return self.reject('repeat_binding_mismatch')
            values = []
            for row in rows:
                z = row.get('impedance_ohm')
                if not isinstance(z, list) or len(z) != 2 or not all(
                        isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in z):
                    return self.reject('invalid_complex_impedance')
                values.append(complex(*z))
            spectra.append(sum(values) / len(values))
        soc = context.get('soc_fraction')
        soc_percent = soc * 100 if isinstance(soc, (int, float)) and not isinstance(soc, bool) else None
        # Caller metadata cannot turn synthetic or new-board data into public training data.
        domain = 'battery_eis_rev_a' if source == 'hardware' else 'simulation'
        sample = dict(source_domain=domain, chemistry=chemistry, soc_percent=soc_percent,
                      temperature_C=context.get('temperature_c'),
                      frequency_hz=[rows[0]['frequency_hz'] for rows in groups],
                      z_real_ohm=[z.real for z in spectra], z_imag_ohm=[z.imag for z in spectra],
                      impedance_unit='ohm', convention='passive_Z_re_plus_j_im',
                      measurement_valid=True, calibration_valid=True)
        return self._annotate(predict(self.model, sample), sample)
