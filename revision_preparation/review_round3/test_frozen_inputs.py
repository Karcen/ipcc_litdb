"""Archive replay must preserve bytes and reject corrupt or unsafe inputs."""
import gzip,hashlib,io,json,tarfile,tempfile,unittest
from pathlib import Path
import restore_frozen_inputs as restore

class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.previous=restore.HERE;restore.HERE=self.root
    def tearDown(self):restore.HERE=self.previous;self.temp.cleanup()
    def fixture(self,name='source/data.csv',raw=b'a,b\n1,2\x00\n'):
        directory=self.root/'frozen_inputs';directory.mkdir()
        buffer=io.BytesIO()
        with tarfile.open(fileobj=buffer,mode='w:gz') as tar:
            item=tarfile.TarInfo(name);item.size=len(raw);tar.addfile(item,io.BytesIO(raw))
        payload=buffer.getvalue();archive=directory/'one.tar.gz';archive.write_bytes(payload)
        member={'path':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'archive':'frozen_inputs/one.tar.gz'}
        manifest={'archives':[{'path':member['archive'],'sha256':hashlib.sha256(payload).hexdigest()}],'members':[member]}
        (directory/'manifest.json').write_text(json.dumps(manifest));return raw,archive
    def test_exact_bytes_and_idempotence(self):
        raw,_=self.fixture();self.assertEqual(restore.ensure_inputs()['restored'],1)
        self.assertEqual((self.root/'source/data.csv').read_bytes(),raw)
        self.assertEqual(restore.ensure_inputs()['restored'],0)
    def test_existing_mismatch_is_not_overwritten(self):
        self.fixture();target=self.root/'source/data.csv';target.parent.mkdir();target.write_bytes(b'author changes')
        with self.assertRaisesRegex(ValueError,'Existing frozen'):restore.ensure_inputs()
        self.assertEqual(target.read_bytes(),b'author changes')
    def test_corrupt_archive_rejected(self):
        _,archive=self.fixture();archive.write_bytes(archive.read_bytes()+b'corrupt')
        with self.assertRaisesRegex(ValueError,'Archive hash'):restore.ensure_inputs()
        self.assertFalse((self.root/'source/data.csv').exists())
    def test_traversal_rejected(self):
        self.fixture('../outside.txt')
        with self.assertRaisesRegex(ValueError,'Unsafe archive'):restore.ensure_inputs()
    def test_selected_prefix_only(self):
        self.fixture();self.assertEqual(restore.ensure_inputs(['source'])['restored'],1)
        with self.assertRaisesRegex(ValueError,'No archived input'):restore.ensure_inputs(['other'])

class SplitRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.previous=restore.HERE;restore.HERE=self.root
    def tearDown(self):restore.HERE=self.previous;self.temp.cleanup()
    def fixture(self):
        directory=self.root/'frozen_inputs';directory.mkdir()
        raw=(b'unchanged compressed bytes\x00'*37)+b'end';chunks=[]
        for index,start in enumerate(range(0,len(raw),256)):
            payload=raw[start:start+256];name=f'frozen_inputs/part_{index}.bin'
            (self.root/name).write_bytes(payload)
            chunks.append({'path':name,'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()})
        descriptor={'paths':['external/sources/pool.csv.gz','external/results/pool.csv.gz'],
            'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'chunks':chunks}
        manifest={'archives':[],'members':[],'split_files':[descriptor]}
        (directory/'manifest.json').write_text(json.dumps(manifest));return raw,manifest
    def test_cold_restore_both_copies_and_idempotence(self):
        raw,manifest=self.fixture();result=restore.ensure_inputs()
        self.assertEqual(result['restored'],2);self.assertEqual(result['verified_split_chunks'],len(manifest['split_files'][0]['chunks']))
        for name in manifest['split_files'][0]['paths']:self.assertEqual((self.root/name).read_bytes(),raw)
        self.assertEqual(restore.ensure_inputs()['restored'],0)
    def test_corrupt_chunk_rejected_before_writing(self):
        _,manifest=self.fixture();chunk=manifest['split_files'][0]['chunks'][0];(self.root/chunk['path']).write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError,'Split chunk hash'):restore.ensure_inputs()
        self.assertTrue(all(not (self.root/name).exists() for name in manifest['split_files'][0]['paths']))
    def test_full_reassembled_hash_is_checked(self):
        _,manifest=self.fixture();manifest['split_files'][0]['sha256']='0'*64
        (self.root/'frozen_inputs/manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError,'Reassembled split file hash'):restore.ensure_inputs()
    def test_existing_split_copy_conflict_is_preserved(self):
        _,manifest=self.fixture();target=self.root/manifest['split_files'][0]['paths'][0];target.parent.mkdir(parents=True);target.write_bytes(b'author edits')
        with self.assertRaisesRegex(ValueError,'Existing frozen'):restore.ensure_inputs()
        self.assertEqual(target.read_bytes(),b'author edits')
    def test_split_destination_traversal_rejected(self):
        _,manifest=self.fixture();manifest['split_files'][0]['paths'][0]='../outside.gz'
        (self.root/'frozen_inputs/manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError,'Unsafe archive'):restore.ensure_inputs()
    def test_chunk_traversal_rejected(self):
        _,manifest=self.fixture();manifest['split_files'][0]['chunks'][0]['path']='../outside.bin'
        (self.root/'frozen_inputs/manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError,'Unsafe archive'):restore.ensure_inputs()
    def test_prefix_restores_only_one_destination(self):
        raw,manifest=self.fixture();names=manifest['split_files'][0]['paths']
        self.assertEqual(restore.ensure_inputs([names[0]])['restored'],1)
        self.assertEqual((self.root/names[0]).read_bytes(),raw);self.assertFalse((self.root/names[1]).exists())
    def test_existing_copy_still_checks_chunk_integrity(self):
        _,manifest=self.fixture();restore.ensure_inputs();chunk=manifest['split_files'][0]['chunks'][0]
        (self.root/chunk['path']).write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError,'Split chunk hash'):restore.ensure_inputs()

class SplitPackageTests(unittest.TestCase):
    def test_deterministic_chunks_and_identical_source_requirement(self):
        import package_frozen_inputs as package
        previous=package.HERE;previous_paths=package.SPLIT_PATHS;previous_size=package.SPLIT_CHUNK_BYTES
        try:
            with tempfile.TemporaryDirectory() as td:
                package.HERE=Path(td);package.SPLIT_PATHS=['one.gz','two.gz'];package.SPLIT_CHUNK_BYTES=256
                for name in package.SPLIT_PATHS:(package.HERE/name).write_bytes(b'payload'*100)
                first=package.split_files();self.assertEqual(first,package.split_files());self.assertEqual(len(first[0]['chunks']),3)
                (package.HERE/'two.gz').write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError,'identical bytes'):package.split_files()
        finally:package.HERE=previous;package.SPLIT_PATHS=previous_paths;package.SPLIT_CHUNK_BYTES=previous_size

if __name__=='__main__':unittest.main()
