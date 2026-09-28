"""Run a short-lived media worker and clean up its child processes."""

import ctypes
from ctypes import wintypes
import os
import signal
import subprocess
import threading

ACTIVE={}
LOCK=threading.Lock()


def stop_workers():
    with LOCK:processes=list(ACTIVE.values())
    for process in processes:
        if process.poll() is not None:continue
        if os.name=='nt':
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,check=False,timeout=10)
        else:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass


def windows_job(process):
    if os.name != 'nt':return None
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    class Basic(ctypes.Structure):
        _fields_=[('user',ctypes.c_int64),('job_user',ctypes.c_int64),('flags',wintypes.DWORD),
                  ('minimum',ctypes.c_size_t),('maximum',ctypes.c_size_t),('active',wintypes.DWORD),
                  ('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_=[(name,ctypes.c_uint64) for name in ['read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes']]
    class Extended(ctypes.Structure):
        _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),
                  ('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
    kernel.CreateJobObjectW.argtypes=[wintypes.LPVOID,wintypes.LPCWSTR];kernel.CreateJobObjectW.restype=wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,wintypes.LPVOID,wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.CreateJobObjectW(None,None)
    info=Extended();info.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not handle:return None
    if not kernel.SetInformationJobObject(handle,9,ctypes.byref(info),ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(handle,int(process._handle)):
        kernel.CloseHandle(handle);return None
    return kernel,handle


def run_worker(command,payload,timeout):
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                             text=True,encoding='utf-8',start_new_session=os.name!='nt',
                             **({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
    job=windows_job(process)
    with LOCK:ACTIVE[process.pid]=process
    try:
        output,error=process.communicate(payload,timeout=timeout)
        return subprocess.CompletedProcess(command,process.returncode,output,error)
    except subprocess.TimeoutExpired:
        if os.name=='nt':
            if job:
                job[0].CloseHandle(job[1]);job=None
            else:subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,check=False,timeout=10)
        else:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        process.kill();process.communicate(timeout=10)
        raise
    finally:
        with LOCK:ACTIVE.pop(process.pid,None)
        if job:job[0].CloseHandle(job[1])
        elif os.name!='nt':
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
