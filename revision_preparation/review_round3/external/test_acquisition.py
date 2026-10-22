#!/usr/bin/env python3
"""Offline tests of exact-byte archiving and failure/credential fences."""
import importlib.util, pathlib, tempfile, unittest, json, threading, contextlib, io
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
P=pathlib.Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('acq',P/'openalex_acquire.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

class Transport:
    def __init__(self,body=None,error=None):self.body=body;self.error=error;self.calls=0
    def fetch(self,url,**kwargs):
        self.calls+=1
        if self.error:raise self.error
        class Response:pass
        r=Response();r.data=self.body;r.status_code=200;return r

class Tests(unittest.TestCase):
    def setup_client(self,path,transport):
        c=m.Client.__new__(m.Client);c.out=path;c.raw=path/'raw';c.raw.mkdir();c.cost=0.;c.cap=.50;c.failed=False;c.key='TEST_CREDENTIAL_NOT_REAL';c.transport=transport;c.skill_sha='test';c.cost_lock=threading.Lock();c.request_locks={}
        return c
    def test_exact_bytes_saved_without_reserializing(self):
        body=b'{  "meta": { "count": 2,"cost_usd":0.001 }, "results": [] }\n'
        with tempfile.TemporaryDirectory() as d:
            c=self.setup_client(pathlib.Path(d),Transport(body));c.fetch('https://api.openalex.org/works?per_page=1&api_key='+c.key)
            p=next(c.raw.glob('*.response.json'));self.assertEqual(p.read_bytes(),body)
            metadata=next(c.raw.glob('*.request.json')).read_text();self.assertNotIn(c.key,metadata);self.assertNotIn('api_key',json.loads(metadata)['parameters'])
    def test_timeout_has_no_retry(self):
        with tempfile.TemporaryDirectory() as d:
            t=Transport(error=TimeoutError());c=self.setup_client(pathlib.Path(d),t)
            with self.assertRaises(m.Stop):c.fetch('https://api.openalex.org/works?api_key='+c.key)
            self.assertEqual(t.calls,1);self.assertTrue((c.out/'API_FAILURE_STOP.json').exists())
            with self.assertRaises(m.Stop):c.fetch('https://api.openalex.org/works')
            self.assertEqual(t.calls,1)
    def test_credential_in_body_is_not_published(self):
        with tempfile.TemporaryDirectory() as d:
            c=self.setup_client(pathlib.Path(d),Transport(b'TEST_CREDENTIAL_NOT_REAL'))
            with self.assertRaises(m.Stop):c.fetch('https://api.openalex.org/works')
            self.assertEqual(list(c.raw.glob('*.response.json')),[])
    def test_local_cap_prevents_request(self):
        with tempfile.TemporaryDirectory() as d:
            t=Transport();c=self.setup_client(pathlib.Path(d),t);c.cost=.50
            with self.assertRaises(m.Stop):c.fetch('https://api.openalex.org/works')
            self.assertEqual(t.calls,0)
    def test_tampered_replay_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            c=self.setup_client(pathlib.Path(d),Transport(b'{"meta":{"count":0},"results":[]}'));c.fetch('https://api.openalex.org/works?per_page=1')
            next(c.raw.glob('*.response.json')).write_bytes(b'{}');c.replay=True
            with self.assertRaises(m.Stop):c.get({'per_page':1})

    def test_interrupted_attempt_consumes_one_of_three_total(self):
        with tempfile.TemporaryDirectory() as d:
            t=Transport(error=TimeoutError());c=self.setup_client(pathlib.Path(d),t);c.retry_limit=2
            key=m.rid('https://api.openalex.org/works',{})
            m.write(c.raw/(key+'.attempt-01.request.json'),{'attempt_number':1,'budget_charge_usd':.001,'error_class':'LocalWorkerCheckpoint'})
            with patch('time.sleep'),self.assertRaises(m.Stop):c.fetch('https://api.openalex.org/works')
            self.assertEqual(t.calls,2)
            self.assertEqual(len(list(c.raw.glob('*.attempt-*.request.json'))),3)
    def test_concurrent_identical_request_is_fetched_once(self):
        from urllib.parse import urlencode
        with tempfile.TemporaryDirectory() as d:
            t=Transport(b'{"meta":{"count":0,"cost_usd":0.001},"results":[]}');c=self.setup_client(pathlib.Path(d),t);c.replay=False
            class FakeCLI:
                def fetch_with_retry(self,url,params,**kw):return json.loads(c.fetch(url+'?'+urlencode(params)).data)
            c.cli=FakeCLI()
            with ThreadPoolExecutor(max_workers=4) as pool:rr=list(pool.map(lambda _:c.get({'per_page':1}),range(4)))
            self.assertEqual(t.calls,1);self.assertEqual(len({r[1] for r in rr}),1)
    def test_reference_absence_and_empty_array_are_distinct(self):
        self.assertEqual(m.reference_status({}),'missing');self.assertEqual(m.reference_status({'referenced_works':None}),'missing')
        self.assertEqual(m.reference_status({'referenced_works':[]}),'empty_array');self.assertEqual(m.reference_status({'referenced_works':['W1']}),'nonempty_array')
    def test_live_cli_requires_explicit_skill_path_before_creating_output(self):
        with tempfile.TemporaryDirectory() as d:
            output=pathlib.Path(d)/'not-created'
            with patch.object(m,'DEFAULT_SKILL',None),patch.object(m.sys,'argv',['openalex_acquire.py','sources','--output',str(output)]),patch.object(m,'Client') as client,contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:m.cli()
            self.assertEqual(error.exception.code,2);self.assertFalse(output.exists());client.assert_not_called()
    def test_live_cli_rejects_missing_skill_file_before_creating_output(self):
        with tempfile.TemporaryDirectory() as d:
            output=pathlib.Path(d)/'not-created'
            with patch.object(m.sys,'argv',['openalex_acquire.py','sources','--skill-cli',str(pathlib.Path(d)/'missing.py'),'--output',str(output)]),patch.object(m,'Client') as client,contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:m.cli()
            self.assertEqual(error.exception.code,2);self.assertFalse(output.exists());client.assert_not_called()
    def test_replay_cli_does_not_require_installed_skill(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(m,'DEFAULT_SKILL',None),patch.object(m.sys,'argv',['openalex_acquire.py','sources','--replay','--output',d]),patch.object(m,'Client') as client,patch.object(m,'source_groups') as groups:
                self.assertEqual(m.cli(),0)
            self.assertIsNone(client.call_args.args[1]);self.assertTrue(client.call_args.args[3]);groups.assert_called_once()

if __name__=='__main__':unittest.main()
