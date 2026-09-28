"""Native integration: download HLS, encrypted HLS and DASH, remux locally."""
import http.client
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from linkexpand.media_resolver import resolve,imported_candidates
from linkexpand.streaming import ffmpeg_path
from linkexpand.video_downloads import DownloadManager


def run():
    ffmpeg=ffmpeg_path()
    if not ffmpeg:raise RuntimeError('FFmpeg is required for the integration test.')
    ffprobe=str(Path(ffmpeg).with_name('ffprobe.exe' if Path(ffmpeg).suffix=='.exe' else 'ffprobe'))
    with tempfile.TemporaryDirectory(prefix='linkexpand-stream-test-') as folder:
        root=Path(folder);fixtures=root/'media';fixtures.mkdir()
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(fixtures),**kwargs)
            def log_message(self,*args):pass
            def copyfile(self,source,output):
                while chunk:=source.read(4096):
                    try:output.write(chunk);output.flush();time.sleep(.01)
                    except OSError:return
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://public.test:{server.server_port}'
        for mode in ['hls','encrypted','dash']:
            directory=fixtures/mode;directory.mkdir()
            command=[ffmpeg,'-hide_banner','-loglevel','error','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=10',
                     '-f','lavfi','-i','sine=frequency=440:sample_rate=44100','-t','4','-c:v','libx264','-preset','ultrafast',
                     '-g','10','-sc_threshold','0','-threads','1','-c:a','aac','-b:a','64k']
            if mode=='dash':command+=['-f','dash','-seg_duration','1','-use_template','1','-use_timeline','1',str(directory/'manifest.mpd')]
            else:
                if mode=='encrypted':
                    key=directory/'key.bin';key.write_bytes(b'0123456789abcdef')
                    info=directory/'keyinfo';info.write_text(base+'/encrypted/key.bin\n'+str(key)+'\n',encoding='utf-8')
                    command+=['-hls_key_info_file',str(info)]
                command+=['-f','hls','-hls_time','1','-hls_playlist_type','vod','-hls_segment_filename',str(directory/'segment-%03d.ts'),str(directory/'index.m3u8')]
            subprocess.run(command,check=True,timeout=30,cwd=directory)
        manager=DownloadManager(root/'downloads')
        # Reuse a real VOD to exercise the extension's separate audio/video merge.
        combined=fixtures/'combined.mp4'
        subprocess.run([ffmpeg,'-loglevel','error','-y','-i',str(fixtures/'hls'/'index.m3u8'),'-c','copy',str(combined)],check=True,timeout=20)
        for kind in ['video','audio']:
            subprocess.run([ffmpeg,'-loglevel','error','-y','-i',str(combined),'-map','0:v:0' if kind=='video' else '0:a:0','-c','copy',
                            str(fixtures/('separate.mp4' if kind=='video' else 'separate.m4a'))],check=True,timeout=20)
        try:
            with patch('linkexpand.video_downloads.public_addresses',return_value=['93.184.216.34']), \
                 patch('linkexpand.video_downloads.PinnedHTTPConnection',side_effect=lambda *args:http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)):
                for mode in ['hls','encrypted','dash','pair']:
                    url=base+'/'+mode+('/manifest.mpd' if mode=='dash' else '/index.m3u8')
                    catalog=imported_candidates(base+'/page',[
                        {'url':base+'/separate.mp4','kind':'video'}, {'url':base+'/separate.m4a','kind':'audio'}]) if mode=='pair' else resolve(url)
                    state=manager.start(catalog['resources'][0],catalog['source']);job=manager.get(state['id'])
                    if mode=='hls':
                        deadline=time.monotonic()+10
                        while not job.stream_state.get('completed') and job.status not in {'complete','error'} and time.monotonic()<deadline:time.sleep(.005)
                        job.pause();manager.close()
                        assert job.status=='paused' and job.stream_state.get('completed'),job.snapshot()
                        count=len(job.stream_state['completed'])
                        manager=DownloadManager(root/'downloads');job=manager.get(state['id'])
                        assert len(job.stream_state['completed'])==count
                        job.launch()
                        print('PASS: saved HLS fragments restored after shutdown and resumed',flush=True)
                    deadline=time.monotonic()+40
                    while job.status not in {'complete','error','cancelled'} and time.monotonic()<deadline:time.sleep(.05)
                    assert job.status=='complete',job.snapshot()
                    result=json.loads(subprocess.check_output([ffprobe,'-v','error','-show_entries','format=duration:stream=codec_type','-of','json',str(job.file)],timeout=10))
                    assert {stream['codec_type'] for stream in result['streams']}=={'video','audio'},result
                    assert 3.8<=float(result['format']['duration'])<=4.3,result
                    print(json.dumps({'PASS':mode,'size':job.file.stat().st_size,'duration':result['format']['duration']}),flush=True)
        finally:
            manager.close();server.shutdown();server.server_close();thread.join()
    print('PASS: HLS, AES-128 HLS, DASH and separate-track merge contain complete video and audio.')

if __name__=='__main__':run()
