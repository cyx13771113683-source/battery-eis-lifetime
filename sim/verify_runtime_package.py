"""Verify the deployed application dependency closure without board access."""
import importlib.util
import json
from pathlib import Path
import tempfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('prepare_runtime',ROOT/'board/prepare_runtime.py')
prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)


def main():
    with tempfile.TemporaryDirectory(prefix='eis-final-runtime-') as temp:
        target=Path(temp)/'runtime'
        prepare.prepare(target,ROOT/'data/fixtures/profile.pending.json',
                        ROOT/'data/fixtures/prototype_calibration.json')
        assert not (target/'runtime.current.log').exists()
        before=(target/'prototype_calibration.json').read_bytes()
        try:
            prepare.prepare(target,ROOT/'data/fixtures/profile.pending.json',
                            ROOT/'data/fixtures/prototype_calibration.json')
        except ValueError:
            pass
        else:
            raise AssertionError('existing runtime must not be overwritten')
        assert (target/'prototype_calibration.json').read_bytes()==before
    print(json.dumps({'status':'PASS','application_version':prepare.VERSION,
                      'dependency_closure_verified':True,'existing_directory_rejected':True,
                      'board_access':False,'test_inputs':'historical replay fixtures'}))


if __name__=='__main__':
    main()
