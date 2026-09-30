"""Independent updater process: wait for exit, replace atomically, restart or roll back."""

import ctypes
import json
import os
from pathlib import Path,PurePosixPath
import secrets
import shutil
import stat
import subprocess
import sys
import time
import urllib.request
import zipfile

from .updater import file_digest,update_directory,version_tuple


def wait_for_exit(pid, timeout=90):
    if type(pid) is not int or pid<=0 or pid==os.getpid():raise ValueError('Invalid updater parent')
    if sys.platform=='win32':
        kernel=ctypes.WinDLL('kernel32');kernel.OpenProcess.restype=ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong];kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=kernel.OpenProcess(0x100000,False,pid)
        if not handle:return
        try:
            if kernel.WaitForSingleObject(handle,int(timeout*1000))!=0:raise RuntimeError('Application did not exit')
        finally:kernel.CloseHandle(handle)
    else:
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            try:os.kill(pid,0)
            except ProcessLookupError:return
            time.sleep(.2)
        raise RuntimeError('Application did not exit')


def checked_bundle(archive, destination):
    with zipfile.ZipFile(archive) as package:
        records=package.infolist()
        if len(records)>20000 or sum(record.file_size for record in records)>2*1024*1024*1024:raise ValueError('Invalid update archive')
        apps=set()
        for record in records:
            path=PurePosixPath(record.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in record.filename:raise ValueError('Unsafe update path')
            for index,part in enumerate(path.parts):
                if part.endswith('.app') and not path.parts[0].startswith('__MACOSX'):apps.add(PurePosixPath(*path.parts[:index+1]))
            mode=record.external_attr>>16
            if stat.S_ISLNK(mode):
                if record.file_size>4096:raise ValueError('Invalid update symlink')
                link=package.read(record).decode()
                resolved=(destination/Path(*path.parts).parent/link).resolve()
                if not resolved.is_relative_to(destination.resolve()):raise ValueError('Unsafe update symlink')
    if len(apps)!=1:raise ValueError('Update must contain one application')
    subprocess.run(['/usr/bin/ditto','-x','-k',str(archive),str(destination)],check=True,timeout=60)
    app=destination/Path(*next(iter(apps)).parts)
    if not (app/'Contents/MacOS/LinkExpand').is_file():raise ValueError('Update application missing')
    subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(app)],check=True,timeout=30)
    return app


def move(source,target):
    deadline=time.monotonic()+30
    while True:
        try:os.replace(source,target);return
        except PermissionError:
            if time.monotonic()>=deadline:raise
            time.sleep(.3)


def launch(target, system, args):
    if system=='darwin':command=[str(target/'Contents/MacOS/LinkExpand')]+args
    else:command=[str(target)]+args
    return subprocess.Popen(command,start_new_session=system!='win32',
        **({'creationflags':subprocess.CREATE_NEW_CONSOLE} if system=='win32' else {}))


def healthy(port, version, timeout=60):
    until=time.monotonic()+timeout;opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic()<until:
        try:
            with opener.open(f'http://127.0.0.1:{port}/api/health',timeout=1) as response:
                result=json.load(response)
            if result.get('app')=='link-expand' and result.get('version')==version:return True
        except (OSError,ValueError):pass
        time.sleep(.5)
    return False


def install(manifest):
    directory=Path(manifest['directory']).resolve();source=Path(manifest['source']).resolve();target=Path(manifest['target']).resolve()
    if not directory.is_relative_to(update_directory().resolve()) or not source.is_relative_to(directory):raise ValueError('Untrusted update staging path')
    system=manifest['platform'];version_tuple(manifest['version'])
    if system!=sys.platform or target.is_relative_to(directory):raise ValueError('Invalid update target')
    if system=='win32' and (target.suffix.lower()!='.exe' or not target.name.lower().startswith('linkexpand')):raise ValueError('Invalid executable target')
    if system=='darwin' and (target.suffix!='.app' or target.name!='LinkExpand.app'):raise ValueError('Invalid application target')
    if file_digest(source)!=manifest['sha256']:raise ValueError('Update checksum mismatch')
    suffix=secrets.token_hex(6);prepared=target.parent/(target.stem+'-update-'+suffix+target.suffix)
    backup=target.parent/(target.name+'.backup-'+suffix)
    installed=False;process=None
    try:
        if system=='darwin':
            bundle=checked_bundle(source,directory/'extracted')
            subprocess.run(['/usr/bin/ditto',str(bundle),str(prepared)],check=True,timeout=60)
        else:shutil.copy2(source,prepared)
        wait_for_exit(manifest['parent_pid'])
        move(target,backup);move(prepared,target);installed=True
        args=['--port',str(manifest['port'])]+manifest.get('restart_args',[])
        if system=='win32' and '--no-browser' not in args:args.append('--no-browser')
        process=launch(target,system,args)
        if not healthy(manifest['port'],manifest['version']):raise RuntimeError('New version did not start')
        if backup.is_dir():shutil.rmtree(backup)
        else:backup.unlink()
        return {'status':'complete','version':manifest['version']}
    except Exception:
        if process and process.poll() is None:
            if system=='win32':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,check=False)
            else:process.terminate()
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
        if backup.exists():
            if installed and target.exists():
                if target.is_dir():shutil.rmtree(target)
                else:target.unlink()
            move(backup,target)
            launch(target,system,['--port',str(manifest['port'])]+manifest.get('restart_args',[]))
            manifest['_rolled_back']=True
        raise
    finally:
        if prepared.exists():
            if prepared.is_dir():shutil.rmtree(prepared)
            else:prepared.unlink()


def main(path):
    manifest_path=Path(path).resolve()
    if not manifest_path.is_relative_to(update_directory().resolve()):raise ValueError('Invalid update manifest')
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    try:result=install(manifest)
    except Exception as error:
        result={'status':'error','error':str(error)}
        # Preflight failures leave the original program intact; reopen it as well.
        target=Path(manifest.get('target',''))
        allowed=(target.suffix.lower()=='.exe' and target.name.lower().startswith('linkexpand')) if sys.platform=='win32' else target.name=='LinkExpand.app'
        if not manifest.get('_rolled_back') and allowed and target.exists():
            try:
                wait_for_exit(manifest['parent_pid'])
                launch(target,manifest['platform'],['--port',str(manifest['port'])]+manifest.get('restart_args',[]))
            except (OSError,KeyError,ValueError,RuntimeError):pass
    (manifest_path.parent/'result.json').write_text(json.dumps(result),encoding='utf-8')
    return 0 if result['status']=='complete' else 1
