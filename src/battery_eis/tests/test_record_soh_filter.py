"""Operator history excludes no-SOH sessions without deleting raw evidence."""
import json
from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ps'))
from ui_records import RecordBrowser,RecordIndex,has_soh

class SohHistoryTests(unittest.TestCase):
    def test_only_numeric_permitted_soh_is_visible(self):
        good=dict(status='prototype_result',soh=.763)
        for ai in (None,{},dict(status='rejected',soh=.763),dict(good,soh=None),dict(good,soh=True),
                   dict(good,soh=float('nan')),dict(good,soh=-.01),dict(good,soh=1.1),
                   dict(status='validated_estimate',soh=.8,valid_for_board=False)):
            self.assertFalse(has_soh(ai))
        for ai in (good,dict(good,soh=0),dict(good,soh=1),dict(status='research_estimate',soh=.8),
                   dict(status='validated_estimate',soh=.8,valid_for_board=True)):
            self.assertTrue(has_soh(ai))

    def test_filter_counts_pagination_refresh_and_raw_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);before={}
            for n in range(13):
                folder=root/('%02d'%n);folder.mkdir()
                (folder/'session.json').write_text('{}')
                ai=dict(status='prototype_result',soh=.763) if n%2 else dict(status='rejected',soh=None)
                (folder/'summary.json').write_text(json.dumps(dict(status='DONE',ai=ai,results=[])))
                (folder/'raw.csv').write_text('retain original data')
                for p in folder.iterdir():before[p]=p.read_bytes()
            browser=RecordBrowser(root);view=browser.view()
            self.assertEqual(view['count'],6);self.assertEqual(view['pages'],2)
            self.assertEqual([r['id'] for r in view['rows']],['11','09','07','05'])
            browser.move(1);self.assertEqual([r['id'] for r in browser.view()['rows']],['03','01'])
            browser.open(0);self.assertEqual(browser.view()['selected']['id'],'03');browser.back()
            for p,data in before.items():self.assertEqual(p.read_bytes(),data)
            self.assertEqual(len(RecordIndex(root).entries),13)
            for n in (1,3):
                path=root/('%02d'%n)/'summary.json'
                path.write_text(json.dumps(dict(status='DONE',ai=dict(status='rejected',soh=None),results=[])))
            browser.refresh();self.assertEqual(browser.view()['count'],4)
            self.assertEqual(browser.view()['page'],0);self.assertEqual(browser.view()['pages'],1)

    def test_empty_and_damaged_history_have_no_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'bad';folder.mkdir()
            (folder/'session.json').write_text('{}');(folder/'summary.json').write_text('bad json')
            broken=root/'bad-session';broken.mkdir()
            (broken/'session.json').write_text('bad json')
            (broken/'summary.json').write_text(json.dumps(dict(ai=dict(status='prototype_result',soh=.8))))
            browser=RecordBrowser(root)
            self.assertEqual(browser.view()['count'],0);self.assertEqual(browser.view()['rows'],[])
            browser.open(0);self.assertIsNone(browser.selected)

    def test_current_run_resets_list_without_deleting_any_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def create(name,soh=.763,complete=True):
                folder=root/name;folder.mkdir()
                (folder/'session.json').write_text('{}')
                (folder/'raw.csv').write_text('original samples')
                if complete:finish(folder,soh)
                return folder
            def finish(folder,soh):
                ai=dict(status='prototype_result' if soh is not None else 'no_result',soh=soh)
                (folder/'summary.json').write_text(json.dumps(dict(status='DONE',ai=ai,results=[])))
            old=create('20990101-old');unfinished=create('20990102-unfinished',complete=False)
            original={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
            browser=RecordBrowser(root,current_run_only=True)
            self.assertEqual(browser.view()['count'],0)
            # New run's wall clock may be earlier than the previous run.
            fresh=create('20250505-new');create('20250505-single',soh=None)
            finish(unfinished,.8);browser.refresh()
            self.assertEqual(browser.view()['count'],1)
            self.assertEqual(browser.view()['rows'][0]['id'],fresh.name)
            browser.open(0);self.assertEqual(browser.selected['id'],fresh.name);browser.back()
            self.assertEqual(RecordBrowser(root,current_run_only=True).view()['count'],0)
            self.assertEqual(len(RecordIndex(root).entries),4)
            for p,contents in original.items():self.assertEqual(p.read_bytes(),contents)

if __name__=='__main__':unittest.main(verbosity=2)
