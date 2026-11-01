"""Prepare a NEW portable v10 runtime directory; no FPGA, services or networking."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import zipfile

HERE=Path(__file__).resolve().parent
VERSION='battery_ui_voltage2v_20261031_v10'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_application():
    app=HERE/'application'
    manifest=json.loads((app/'manifest.json').read_text())
    assert manifest['version']==VERSION
    actual={p.relative_to(app).as_posix() for p in app.rglob('*')
            if p.is_file() and p.name!='manifest.json' and '__pycache__' not in p.parts}
    assert actual==set(manifest['files']), 'application file set'
    for name,expected in manifest['files'].items():
        assert sha(app/name)==expected, name


def prepare(root,profile,calibration):
    root=Path(root).absolute()
    if root.exists():
        raise ValueError('output must be a new directory; existing board files are never overwritten')
    if any(c.isspace() for c in str(root)):
        raise ValueError('use a path without whitespace')
    profile=Path(profile).resolve(strict=True)
    calibration=Path(calibration).resolve(strict=True)
    verify_application()
    with zipfile.ZipFile(HERE/'base_release.zip') as bundle:
        for item in bundle.infolist():
            p=PurePosixPath(item.filename)
            if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0]!='battery_eis_rev_a':
                raise ValueError('base release path')
            if stat.S_ISLNK(item.external_attr>>16):
                raise ValueError('base release symlink')
        root.mkdir(parents=True,exist_ok=False)
        bundle.extractall(root/'program')
    frozen=root/'program/battery_eis_rev_a'
    release=frozen/'release/be03_offline_20261023_final'
    source=json.loads((release/'source_manifest.json').read_text())
    for name,expected in source.items():
        assert sha(frozen/name)==expected, 'base source: '+name
    runtime=root/'runtime';runtime.mkdir()
    for name in ('battery_eis.bit','battery_eis.hwh','manifest.json'):
        shutil.copy2(release/name,runtime/name)
    shutil.copy2(profile,root/'profile.pending.json')
    shutil.copy2(calibration,root/'prototype_calibration.json')
    shutil.copytree(HERE/'application',root/VERSION)
    command=[sys.executable,'-B',str(root/VERSION/'prototype_start.py'),
             '--root',str(root),'--verify-only']
    subprocess.run(command,check=True)
    print('RUNTIME_PREPARED',str(root),flush=True)
    return root


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--profile',type=Path,required=True)
    parser.add_argument('--calibration',type=Path,required=True)
    args=parser.parse_args()
    prepare(args.output,args.profile,args.calibration)


if __name__=='__main__':
    main()
