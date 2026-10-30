"""Computer-only maintenance of prototype ratio parameters; no hardware access."""
from datetime import datetime,timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

HERE=Path(__file__).resolve().parent
if HERE.name=='scripts':HERE=HERE.parent
sys.path.insert(0,str(HERE/'ps'))
from prototype import PrototypeCalibration


def save(path,document):
    PrototypeCalibration(document)
    payload=(json.dumps(document,sort_keys=True,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    versions=path.parent/'prototype_calibration_versions';versions.mkdir(exist_ok=True)
    if path.exists():
        old=path.read_bytes();oldpath=versions/(hashlib.sha256(old).hexdigest()+'.json')
        if not oldpath.exists():oldpath.write_bytes(old)
    checksum=hashlib.sha256(payload).hexdigest();version=versions/(checksum+'.json')
    if not version.exists():version.write_bytes(payload)
    temporary=path.with_name('.prototype-'+uuid.uuid4().hex+'.tmp')
    with temporary.open('xb') as stream:stream.write(payload);stream.flush();os.fsync(stream.fileno())
    temporary.chmod(0o600);os.replace(temporary,path)
    return dict(id=document['id'],sha256=checksum,path=str(path))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('show','install'))
    parser.add_argument('--path',type=Path,required=True);parser.add_argument('--input',type=Path)
    args=parser.parse_args()
    if args.command=='show':
        calibration=PrototypeCalibration.load(args.path)
        print(json.dumps(calibration.document,ensure_ascii=False,indent=2))
    else:
        if args.input is None:parser.error('--input required')
        print(json.dumps(save(args.path,json.loads(args.input.read_text(encoding='utf-8-sig'))),ensure_ascii=False))


if __name__=='__main__':main()
