import hashlib,json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch,Mock
from linkexpand.metadata import PreviewError,Resource
from linkexpand.updater import select_release,version_tuple,Updater
from linkexpand.update_worker import install,checked_bundle

class UpdateTests(unittest.TestCase):
 def release(self):
  name='LinkExpand-0.4.99-Windows-x64.exe'
  return {'tag_name':'v0.4.99','assets':[{'name':name,'size':1024,'digest':'sha256:'+'a'*64,'browser_download_url':'https://github.com/cuterica/link-expand/releases/download/v0.4.99/'+name}]}
 def test_numeric_versions_and_correct_asset(self):
  self.assertGreater(version_tuple('v0.3.10'),version_tuple('0.3.9'))
  self.assertEqual(select_release(self.release(),'win32')['version'],'0.4.99')
 def test_untrusted_url_digest_and_prerelease_are_rejected(self):
  for field,value in [('digest','sha256:wrong'),('browser_download_url','https://evil.example/a.exe'),('size',-1)]:
   data=self.release();data['assets'][0][field]=value
   with self.assertRaises(PreviewError):select_release(data,'win32')
  data=self.release();data['prerelease']=True
  with self.assertRaises(PreviewError):select_release(data,'win32')
 def test_check_reads_public_latest_without_installing(self):
  response=Resource('https://api.github.com/repos/cuterica/link-expand/releases/latest',json.dumps(self.release()).encode(),'application/json')
  with patch('linkexpand.updater.sys.platform','win32'),patch('linkexpand.updater.fetch_resource',return_value=response):
   state=Updater(frozen=True).check()
  self.assertEqual(state['state'],'available');self.assertTrue(state['supported'])
 def test_archive_traversal_rejected_before_extraction(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'update.zip'
   with zipfile.ZipFile(path,'w') as archive:archive.writestr('../outside.txt','bad')
   with self.assertRaises(ValueError):checked_bundle(path,Path(folder)/'out')
 def manifest(self,root):
  stage=root/'stage';stage.mkdir();source=stage/'new.exe';source.write_bytes(b'new version')
  target=root/'LinkExpand-test.exe';target.write_bytes(b'old version')
  return {'directory':str(stage),'source':str(source),'target':str(target),'platform':'win32','sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'version':'0.4.99','port':8766,'parent_pid':12345}
 def test_replacement_is_atomic_and_verified(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);manifest=self.manifest(root)
   with patch('linkexpand.update_worker.update_directory',return_value=root),patch('linkexpand.update_worker.sys.platform','win32'),patch('linkexpand.update_worker.wait_for_exit'),patch('linkexpand.update_worker.launch',return_value=Mock()),patch('linkexpand.update_worker.healthy',return_value=True):
    result=install(manifest)
   self.assertEqual(result['status'],'complete');self.assertEqual(Path(manifest['target']).read_bytes(),b'new version')
   self.assertFalse(list(root.glob('*.backup-*')))
 def test_failed_start_restores_old_file(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);manifest=self.manifest(root);process=Mock();process.poll.return_value=1
   with patch('linkexpand.update_worker.update_directory',return_value=root),patch('linkexpand.update_worker.sys.platform','win32'),patch('linkexpand.update_worker.wait_for_exit'),patch('linkexpand.update_worker.launch',return_value=process),patch('linkexpand.update_worker.healthy',return_value=False):
    with self.assertRaises(RuntimeError):install(manifest)
   self.assertEqual(Path(manifest['target']).read_bytes(),b'old version')
 def test_checksum_failure_preserves_original(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);manifest=self.manifest(root);manifest['sha256']='b'*64
   with patch('linkexpand.update_worker.update_directory',return_value=root),patch('linkexpand.update_worker.sys.platform','win32'):
    with self.assertRaises(ValueError):install(manifest)
   self.assertEqual(Path(manifest['target']).read_bytes(),b'old version')
