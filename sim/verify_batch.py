"""Verify all 22 archived attempts and recompute the twenty-cell summary offline."""
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import zipfile

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'data/batch_20'

def main():
    attempts=[json.loads(s) for s in (BASE/'attempts.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(attempts)==22
    assert len({(d['battery_id'],d['attempt']) for d in attempts})==22
    selected=[d for d in attempts if d['status']=='DONE']
    assert len(selected)==20
    with (BASE/'results.csv').open(encoding='utf-8-sig',newline='') as f:
        table=list(csv.reader(f))
    assert len(table)==21
    differences={f:[] for f in (80.,17.8,2.16)}
    for d in attempts:
        folder=BASE/'raw'/d['battery_id'].lower()/('attempt_%02d'%d['attempt'])
        archive=folder/'session.zip'
        assert hashlib.sha256(archive.read_bytes()).hexdigest()==d['archive_sha256']
        manifest=json.loads((folder/'archive_manifest.json').read_text(encoding='utf-8'))
        with zipfile.ZipFile(archive) as z:
            assert z.testzip() is None and set(z.namelist())==set(manifest['files'])
            for name,digest in manifest['files'].items():
                assert hashlib.sha256(z.read(name)).hexdigest()==digest
            summary=json.loads(z.read('summary.json'))
            assert summary['status']==d['status']
            if d['status']!='DONE':
                assert d['soh_percent'] is None
                continue
            row=table[int(d['battery_id'][1:])]
            assert row[0]==d['battery_id'] and row[-1]=='完成'
            assert len(d['frequency_repeats'])==len(summary['results'])==6
            for i,f in enumerate((80.,17.8,2.16)):
                rs=[x for x in d['frequency_repeats'] if x['requested_frequency_hz']==f]
                assert len(rs)==2 and {x['repeat'] for x in rs}=={0,1}
                mean=sum(complex(x['Re_ohm'],x['Im_ohm']) for x in rs)/2
                assert math.isclose(float(row[i+2]),abs(mean)*1000,abs_tol=0.00000051)
                magnitudes=[]
                for x in rs:
                    assert x['quality_ok']
                    assert json.loads(z.read(x['capture_metadata']))['faults']==0
                    source=next(t for t in summary['results'] if t['raw_file']==x['raw_file'])
                    assert len(z.read(x['raw_file']).splitlines())-1==sum(source['counts'])
                    magnitudes.append(abs(complex(x['Re_ohm'],x['Im_ohm'])))
                differences[f].append(abs(magnitudes[0]-magnitudes[1])/statistics.mean(magnitudes)*100)
            assert math.isclose(float(row[5]),summary['ai']['soh']*100,abs_tol=0.00000051)
            assert d['zero_output_after'] and d['zero_output_evidence']['zero_ack']
    times=[d['measurement_elapsed_s'] for d in selected]
    result=dict(status='PASS',completed_cells=20,attempts=22,preserved_failures=2,
                valid_frequency_captures=120,mean_elapsed_s=statistics.mean(times),
                maximum_elapsed_s=max(times),
                repeat_magnitude_difference_percent={str(f):dict(mean=statistics.mean(v),maximum=max(v)) for f,v in differences.items()},
                capacity_comparison_source='project_owner_confirmation_not_recomputed')
    print(json.dumps(result,indent=2))
    return result

if __name__=='__main__':
    main()
