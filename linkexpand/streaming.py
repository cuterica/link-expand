"""Finite HLS/DASH planning, bounded segment transfer and local-only remuxing."""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.parse import urljoin,urlsplit
import xml.etree.ElementTree as ET

from .download_http import resource_url,document_body
from .metadata import PreviewError
from .quality import quality_rank

MAX_ITEMS=10000


def ffmpeg_path():
    configured=os.environ.get('LINK_EXPAND_FFMPEG')
    if configured and Path(configured).is_file():return configured
    executable=shutil.which('ffmpeg')
    if executable:return executable
    import sys
    if sys.platform=='darwin':
        candidates=[Path('/opt/homebrew/bin/ffmpeg'),Path('/usr/local/bin/ffmpeg'),Path('/opt/local/bin/ffmpeg'),
                    Path(sys.executable).parent/'ffmpeg',Path.home()/'Library'/'Application Support'/'LinkExpand'/'tools'/'ffmpeg']
        for path in candidates:
            if path.is_file() and os.access(path,os.X_OK):return str(path)
    if os.name=='nt':
        candidates=[Path(sys.executable).parent/'ffmpeg.exe',Path(sys.executable).parent/'tools'/'ffmpeg.exe',
                    Path(os.environ['LOCALAPPDATA'])/'LinkExpand'/'tools'/'ffmpeg.exe']
        candidates+=sorted((Path(os.environ['LOCALAPPDATA'])/'Microsoft'/'WinGet'/'Packages').glob('*FFmpeg*/ffmpeg*/bin/ffmpeg.exe'),reverse=True)
        for path in candidates:
            if path.is_file():return str(path)
    return None


def document(url,headers=None,credential_origin=None,limit=2*1024*1024):
    from .video_downloads import open_video
    with open_video(url,request_headers=headers,credential_origin=credential_origin,allow_compressed=True) as response:
        return response.resource_url,document_body(response,limit).decode('utf-8-sig',errors='replace')


def attributes(text):
    return {key:value[1:-1] if value.startswith('"') else value
            for key,value in re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)',text)}


def byte_range(text,previous_end=0):
    match=re.fullmatch(r'(\d+)(?:@(\d+))?',text)
    if not match or int(match[1])<=0:raise PreviewError('播放清单的字节范围无效。')
    start=int(match[2]) if match[2] else previous_end
    return [start,start+int(match[1])-1]


def hls_media(text,url):
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0]!='#EXTM3U':raise PreviewError('HLS 播放清单无效。')
    if '#EXT-X-ENDLIST' not in lines:raise PreviewError('这是持续更新的直播流，目前只处理有完整长度的点播内容。')
    segments=[];key=None;initialization=None;duration=0.;sequence=0;pending_range=None;last_end={};discontinuity=False
    for line in lines:
        if line.startswith('#EXT-X-MEDIA-SEQUENCE:'):sequence=int(line.split(':',1)[1])
        elif line.startswith('#EXTINF:'):duration=float(line.split(':',1)[1].split(',')[0])
        elif line.startswith('#EXT-X-BYTERANGE:'):pending_range=line.split(':',1)[1]
        elif line.startswith('#EXT-X-DISCONTINUITY'):discontinuity=True
        elif line.startswith('#EXT-X-KEY:'):
            data=attributes(line.split(':',1)[1]);method=data.get('METHOD')
            if method=='NONE':key=None
            elif method=='AES-128' and data.get('KEYFORMAT','identity')=='identity':
                if not data.get('URI'):raise PreviewError('HLS 加密清单缺少公开密钥地址。')
                key={'url':resource_url(urljoin(url,data['URI'])),'iv':data.get('IV')}
                if key['iv'] and not re.fullmatch(r'0[xX][0-9a-fA-F]{1,32}',key['iv']):raise PreviewError('HLS IV 格式无效。')
            else:raise PreviewError('此播放清单使用受保护或不支持的加密格式，无法通用下载。')
        elif line.startswith('#EXT-X-MAP:'):
            data=attributes(line.split(':',1)[1]);target=resource_url(urljoin(url,data['URI']))
            initialization={'url':target,'range':byte_range(data['BYTERANGE']) if data.get('BYTERANGE') else None}
        elif not line.startswith('#'):
            target=resource_url(urljoin(url,line));interval=byte_range(pending_range,last_end.get(target,0)) if pending_range else None
            if interval:last_end[target]=interval[1]+1
            if not math.isfinite(duration) or duration<=0:raise PreviewError('HLS 分段时长无效。')
            segments.append({'url':target,'range':interval,'duration':duration,'key':key,'init':initialization,
                             'sequence':sequence+len(segments),'discontinuity':discontinuity})
            pending_range=None;duration=0.;discontinuity=False
            if len(segments)>MAX_ITEMS:raise PreviewError('播放清单的分段数量超过限制。')
    if not segments:raise PreviewError('播放清单不包含媒体分段。')
    return {'kind':'video','segments':segments,'duration':sum(item['duration'] for item in segments)}


def hls_variants(text,url):
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    renditions={};variants=[]
    for index,line in enumerate(lines):
        if line.startswith('#EXT-X-MEDIA:'):
            attrs=attributes(line.split(':',1)[1])
            if attrs.get('TYPE')=='AUDIO' and attrs.get('URI'):
                group=attrs.get('GROUP-ID','');record=dict(attrs,url=resource_url(urljoin(url,attrs['URI'])))
                if group not in renditions or attrs.get('DEFAULT')=='YES':renditions[group]=record
        elif line.startswith('#EXT-X-STREAM-INF:'):
            attrs=attributes(line.split(':',1)[1])
            following=next((value for value in lines[index+1:] if not value.startswith('#')),None)
            if following:
                bandwidth=int(attrs.get('AVERAGE-BANDWIDTH') or attrs.get('BANDWIDTH') or 0)
                variants.append({'url':resource_url(urljoin(url,following)),'kind':'hls','bandwidth':bandwidth,
                                 'quality':attrs.get('RESOLUTION') or (f'{bandwidth//1000} kbps' if bandwidth else 'HLS'),
                                 'frame_rate':attrs.get('FRAME-RATE'), 'audio_group':attrs.get('AUDIO')})
    for variant in variants:
        if variant['audio_group'] in renditions:variant['audio_url']=renditions[variant['audio_group']]['url']
    return sorted(variants,key=quality_rank,reverse=True)


def hls_plan(url,headers=None,credential_origin=None,depth=0):
    if depth>4:raise PreviewError('嵌套播放清单层级过深。')
    final,text=document(url,headers,credential_origin)
    variants=hls_variants(text,final)
    if variants:
        selected=variants[0]
        plan=hls_plan(selected['url'],headers,credential_origin,depth+1)
        if selected.get('audio_url'):
            audio=hls_plan(selected['audio_url'],headers,credential_origin,depth+1)
            for track in audio['tracks']:track['kind']='audio'
            plan['tracks']+=audio['tracks']
        plan['quality']=selected['quality'];plan['bandwidth']=selected['bandwidth']
        return plan
    return {'kind':'hls','tracks':[hls_media(text,final)],'quality':'HLS','bandwidth':0}


def children(node,name):return [item for item in node if item.tag.rsplit('}',1)[-1]==name]
def child(node,name):return next(iter(children(node,name)),None)
def duration_seconds(value):
    match=re.fullmatch(r'P(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?',value or '')
    return sum(float(number or 0)*scale for number,scale in zip(match.groups(),[86400,3600,60,1])) if match else 0
def with_base(node,base):
    item=child(node,'BaseURL')
    return resource_url(urljoin(base,item.text.strip())) if item is not None and item.text else base


def substitute(template,representation,number=None,time_value=None):
    template=template.replace('$$','\x00')
    values={'RepresentationID':representation.get('id',''),'Bandwidth':representation.get('bandwidth','0'),
            'Number':number,'Time':time_value}
    def replace(match):
        value=values[match[1]]
        if value is None:raise PreviewError('DASH 清单缺少分段定位信息。')
        return str(value).zfill(int(match[2])) if match[2] else str(value)
    return re.sub(r'\$(RepresentationID|Bandwidth|Number|Time)(?:%0(\d+)d)?\$',replace,template).replace('\x00','$')


def template_segments(template,attrs,base,representation,period_duration):
    timescale=int(attrs.get('timescale','1'));number=int(attrs.get('startNumber','1'))
    if timescale<=0:raise PreviewError('DASH 时间基准无效。')
    init=attrs.get('initialization');initialization={'url':resource_url(urljoin(base,substitute(init,representation))),'range':None} if init else None
    timeline=child(template,'SegmentTimeline');points=[]
    if timeline is not None:
        elements=children(timeline,'S');timestamp=0
        for index,item in enumerate(elements):
            timestamp=int(item.get('t',timestamp));length=int(item.get('d','0'));repeat=int(item.get('r','0'))
            if length<=0:raise PreviewError('DASH 分段时长无效。')
            if repeat<0:
                next_time=int(elements[index+1].get('t')) if index+1<len(elements) and elements[index+1].get('t') else int(period_duration*timescale)+int(attrs.get('presentationTimeOffset','0'))
                if next_time<=timestamp:raise PreviewError('DASH 分段没有有限长度。')
                repeat=math.ceil((next_time-timestamp)/length)-1
            if len(points)+repeat+1>MAX_ITEMS:raise PreviewError('DASH 分段数量超过限制。')
            for _ in range(repeat+1):points.append((timestamp,length/timescale));timestamp+=length
    else:
        length=int(attrs.get('duration','0'))
        if length<=0 or period_duration<=0:raise PreviewError('DASH 清单没有有限分段时长。')
        count=math.ceil(period_duration*timescale/length)
        if count>MAX_ITEMS:raise PreviewError('DASH 分段数量超过限制。')
        points=[(index*length,length/timescale) for index in range(count)]
    media=attrs.get('media')
    if not media:raise PreviewError('DASH 清单缺少媒体模板。')
    return [{'url':resource_url(urljoin(base,substitute(media,representation,number+index,timestamp))),
             'range':None,'duration':span,'init':initialization,'key':None,'sequence':index,'discontinuity':False}
            for index,(timestamp,span) in enumerate(points)]


def dash_plan(url,headers=None,credential_origin=None,video_id=None,_content=None):
    final,text=_content if _content else document(url,headers,credential_origin)
    if re.search(r'<!DOCTYPE|<!ENTITY',text,re.I):raise PreviewError('不接受含实体声明的 DASH 清单。')
    try:root=ET.fromstring(text)
    except ET.ParseError:raise PreviewError('DASH 播放清单格式无效。') from None
    if root.tag.rsplit('}',1)[-1]!='MPD' or root.get('type','static')!='static':raise PreviewError('目前只处理有限长度的 DASH 点播清单。')
    periods=children(root,'Period')
    if not periods:raise PreviewError('DASH 清单不包含 Period。')
    if len(periods)>1:
        grouped={};expected_kinds=None;total_duration=duration_seconds(root.get('mediaPresentationDuration'));quality='';bandwidth=0
        for index,original in enumerate(periods):
            local=ET.Element(root.tag,dict(root.attrib))
            for element in children(root,'BaseURL'):local.append(ET.fromstring(ET.tostring(element)))
            period=ET.fromstring(ET.tostring(original));start=duration_seconds(original.get('start'))
            span=duration_seconds(original.get('duration'))
            if not span:
                next_start=duration_seconds(periods[index+1].get('start')) if index+1<len(periods) else total_duration
                span=next_start-start
            if span<=0:raise PreviewError('多 Period DASH 清单缺少完整时长。')
            period.set('duration',f'PT{span}S');local.append(period)
            piece=dash_plan(url,headers,credential_origin,video_id if index==0 else None,(final,ET.tostring(local,encoding='unicode')))
            kinds={track['kind'] for track in piece['tracks']}
            if expected_kinds is not None and kinds!=expected_kinds:raise PreviewError('不同 Period 的音视频轨不一致，无法可靠合并。')
            expected_kinds=kinds;quality=quality or piece['quality'];bandwidth=max(bandwidth,piece['bandwidth'])
            for track in piece['tracks']:
                if track['kind'] not in grouped:grouped[track['kind']]=track
                else:
                    target=grouped[track['kind']];track['segments'][0]['discontinuity']=True
                    offset=len(target['segments'])
                    for number,segment in enumerate(track['segments']):segment['sequence']=offset+number
                    target['segments']+=track['segments'];target['duration']+=track['duration']
        return {'kind':'dash','tracks':list(grouped.values()),'quality':quality,'bandwidth':bandwidth}
    period=periods[0];duration=duration_seconds(period.get('duration') or root.get('mediaPresentationDuration'))
    base=with_base(period,with_base(root,final));tracks=[];quality=[];bandwidth=0
    for adaptation in sorted(children(period,'AdaptationSet'),key=lambda node:max((quality_rank(dict(node.attrib,**item.attrib)) for item in children(node,'Representation')),default=(0,0,0,0)),reverse=True):
        representations=children(adaptation,'Representation')
        mime=adaptation.get('mimeType') or next((item.get('mimeType') for item in representations if item.get('mimeType')),'')
        kind=adaptation.get('contentType') or ('audio' if 'audio' in mime else 'video' if 'video' in mime else '')
        if kind not in {'video','audio'}:continue
        if any(track['kind']==kind for track in tracks):continue
        reps=sorted(children(adaptation,'Representation'),key=lambda item:quality_rank(dict(adaptation.attrib,**item.attrib)),reverse=True)
        if kind=='video' and video_id and not any(item.get('id')==video_id for item in reps):continue
        selected=next((item for item in reps if not children(item,'ContentProtection') and not children(adaptation,'ContentProtection')
                       and (kind!='video' or not video_id or item.get('id')==video_id)),None)
        if selected is None:raise PreviewError('DASH 音视频含播放保护，不能通用下载。')
        repbase=with_base(selected,with_base(adaptation,base));template=child(selected,'SegmentTemplate')
        parent_template=child(adaptation,'SegmentTemplate')
        if parent_template is None:parent_template=child(period,'SegmentTemplate')
        if template is None:template=parent_template
        if template is not None:
            attrs=dict(parent_template.attrib) if parent_template is not None else {};attrs.update(template.attrib)
            if parent_template is not None and template is not parent_template and child(template,'SegmentTimeline') is None:
                inherited=ET.fromstring(ET.tostring(template))
                timeline=child(parent_template,'SegmentTimeline')
                if timeline is not None:inherited.append(ET.fromstring(ET.tostring(timeline)))
                template=inherited
            segments=template_segments(template,attrs,repbase,selected,duration)
        else:
            listing=child(selected,'SegmentList')
            if listing is None:listing=child(adaptation,'SegmentList')
            if listing is None:
                segment_base=child(selected,'SegmentBase')
                if segment_base is None:segment_base=child(adaptation,'SegmentBase')
                if segment_base is not None:
                    segments=[{'url':repbase,'range':None,'duration':duration or 1,'init':None,'key':None,'sequence':0,'discontinuity':False}]
                else:raise PreviewError('不支持这个 DASH 分段定位方式。')
            else:
                init=child(listing,'Initialization');initialization=None
                if init is not None:
                    interval=[int(value) for value in init.get('range').split('-')] if init.get('range') else None
                    initialization={'url':resource_url(urljoin(repbase,init.get('sourceURL',''))),'range':interval}
                entries=children(listing,'SegmentURL');timescale=int(listing.get('timescale','1'));span=int(listing.get('duration','0'))/timescale if timescale>0 else 0
                if len(entries)>MAX_ITEMS or not entries:raise PreviewError('DASH 分段列表无效。')
                span=span or duration/len(entries) or 1
                segments=[{'url':resource_url(urljoin(repbase,item.get('media',''))),'range':[int(value) for value in item.get('mediaRange').split('-')] if item.get('mediaRange') else None,
                           'duration':span,'init':initialization,'key':None,'sequence':index,'discontinuity':False} for index,item in enumerate(entries)]
        tracks.append({'kind':kind,'segments':segments,'duration':sum(item['duration'] for item in segments)})
        bandwidth+=int(selected.get('bandwidth','0'))
        if kind=='video':quality.append(f'{selected.get("width",adaptation.get("width","?"))}×{selected.get("height",adaptation.get("height","?"))}')
        # One video and one default audio track are enough for the chosen presentation.
        if len(tracks)>=2 and {item['kind'] for item in tracks}>={'video','audio'}:break
    if not tracks:raise PreviewError('DASH 清单没有可处理的音视频轨。')
    return {'kind':'dash','tracks':tracks,'quality':' + '.join(quality) or 'DASH','bandwidth':bandwidth}


def stream_plan(variant,headers,credential_origin):
    if variant.get('kind')=='pair':
        return {'kind':'pair','quality':'音视频合并','bandwidth':0,'tracks':[
            {'kind':'video','duration':0,'segments':[{'url':variant['url'],'range':None,'duration':1,'init':None,'key':None,'sequence':0,'discontinuity':False}],
             'direct':True,'headers':headers,'credential_origin':credential_origin},
            {'kind':'audio','duration':0,'segments':[{'url':variant['audio_url'],'range':None,'duration':1,'init':None,'key':None,'sequence':0,'discontinuity':False}],
             'direct':True,'headers':variant.get('audio_headers',{}),'credential_origin':variant.get('audio_origin') or variant['audio_url']}]}
    if variant.get('kind')=='dash':return dash_plan(variant['url'],headers,credential_origin,variant.get('dash_video_id'))
    plan=hls_plan(variant['url'],headers,credential_origin)
    if variant.get('audio_url'):
        audio=hls_plan(variant['audio_url'],headers,credential_origin)
        for track in audio['tracks']:track['kind']='audio'
        plan['tracks']+=audio['tracks']
    return plan


def resource_key(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()[:24]


def segment_digest(path, job):
    digest=hashlib.sha256()
    with path.open('rb') as source:
        while chunk:=source.read(1024*1024):
            job.check_stop();digest.update(chunk)
    return digest.hexdigest()


def stream_download(job,variant):
    from .video_downloads import open_video,DownloadStopped,RetryableHTTP
    executable=ffmpeg_path()
    if not executable:raise PreviewError('HLS / DASH 合并需要免费的 FFmpeg。请安装 FFmpeg，或将 ffmpeg.exe 放到程序旁边。')
    headers=job.request_headers(variant);scope=job.video.get('credential_origin') or job.source
    plan=stream_plan(variant,headers,scope)
    estimate=max(track['duration'] for track in plan['tracks'])*plan.get('bandwidth',0)/8
    if job.limit is not None and estimate>job.limit*1.15:raise PreviewError('这个清晰度预计超过 500 MB，请选择更低清晰度。')
    folder=job.manager.storage/(job.id+'-segments')
    if folder.is_symlink():raise PreviewError('下载分段文件夹不能是符号链接。')
    folder.mkdir(exist_ok=True)
    fingerprint=resource_key(plan)
    if job.stream_state.get('fingerprint')!=fingerprint:
        shutil.rmtree(folder);folder.mkdir();job.stream_state={'fingerprint':fingerprint,'completed':{}}
    resources={}
    def register(value):
        if not value:return None
        name=resource_key({'url':value['url'],'range':value.get('range')})
        original=Path(urlsplit(value['url']).path).suffix.lower()
        suffix='.key' if value.get('is_key') else '.mp4' if value.get('is_init') else original if original in {'.ts','.m4s','.mp4','.m4a','.aac','.mp3','.webm'} else '.bin'
        name+=suffix;resources[name]=value;return name
    for track in plan['tracks']:
        for segment in track['segments']:
            segment['request_headers']=track.get('headers',headers)
            segment['credential_origin']=track.get('credential_origin',scope)
            segment['local']=register(segment)
            segment['init_local']=register(dict(segment['init'],is_init=True)) if segment.get('init') else None
            segment['key_local']=register(dict(segment['key'],is_key=True)) if segment.get('key') else None
    completed=job.stream_state['completed']
    for name in list(completed):
        path=folder/name
        if name not in resources or path.is_symlink() or not path.is_file() or path.stat().st_size!=completed[name]['size'] or segment_digest(path,job)!=completed[name]['sha256']:
            completed.pop(name,None)
    job.downloaded=sum(value['size'] for value in completed.values());job.asset={'url':variant['url'],'size':None,'range':False,'validator':'segments'}
    job.quality=variant.get('quality') or plan['quality'];job.status='downloading';job.save()
    live={};abort=__import__('threading').Event()
    def fetch(name,item):
        if name in completed:return
        temporary=folder/(name+'.tmp');target=folder/name
        extra={}
        if item.get('range'):extra['Range']=f'bytes={item["range"][0]}-{item["range"][1]}'
        for attempt in range(3):
            job.check_stop()
            if abort.is_set():return
            with job.lock:live[name]=0
            try:
                digest=hashlib.sha256();size=0
                with open_video(item['url'],extra,item.get('request_headers',headers),item.get('credential_origin',scope),transport=job.transport) as response,temporary.open('wb') as output:
                    if item.get('range'):
                        expected=f'bytes {item["range"][0]}-{item["range"][1]}/'
                        if response.status!=206 or not response.getheader('Content-Range','').startswith(expected):raise PreviewError('流媒体字节范围响应不匹配。')
                    while chunk:=response.read1(65536):
                        job.check_stop()
                        if abort.is_set():return
                        job.throttle(len(chunk))
                        with job.lock:
                            total=sum(record['size'] for record in completed.values())+sum(live.values())+len(chunk)
                            if job.exceeds_limit(total):raise PreviewError('分段资源合计超过 500 MB，已停止下载。')
                            live[name]+=len(chunk);job.downloaded=total
                        output.write(chunk);digest.update(chunk);size+=len(chunk);job.meter.add(len(chunk))
                    output.flush();os.fsync(output.fileno())
                if item.get('range') and size!=item['range'][1]-item['range'][0]+1:raise OSError('Incomplete segment range')
                if item.get('is_key') and size!=16:raise PreviewError('HLS AES-128 密钥长度无效。')
                temporary.replace(target)
                with job.lock:
                    live.pop(name,None);completed[name]={'size':size,'sha256':digest.hexdigest()};job.downloaded=sum(value['size'] for value in completed.values())+sum(live.values());job.save()
                return
            except (OSError,http.client.HTTPException,RetryableHTTP) as error:
                job.check_stop()
                temporary.unlink(missing_ok=True)
                if attempt==2:raise PreviewError('流媒体分段下载中断，可继续下载已完成的分段。') from None
                job.stop.wait(max(.3*(attempt+1),getattr(error,'retry_after',0)))
            finally:
                temporary.unlink(missing_ok=True)
                with job.lock:live.pop(name,None)
    job.parallel_work(list(resources),lambda name,worker:fetch(name,resources[name]),abort)
    job.check_stop();job.status='merging';job.save()
    inputs=[]
    for index,track in enumerate(plan['tracks']):
        if track.get('direct'):
            inputs.append((folder/track['segments'][0]['local'],track['kind']));continue
        lines=['#EXTM3U','#EXT-X-VERSION:6',f'#EXT-X-TARGETDURATION:{math.ceil(max(segment["duration"] for segment in track["segments"]))}',
               f'#EXT-X-MEDIA-SEQUENCE:{track["segments"][0]["sequence"]}']
        old_key=None;old_init=None
        for segment in track['segments']:
            if segment['discontinuity']:lines.append('#EXT-X-DISCONTINUITY')
            current_key=(segment['key_local'],segment['key'].get('iv')) if segment['key_local'] else None
            if current_key!=old_key:
                if segment['key_local']:
                    declaration=f'#EXT-X-KEY:METHOD=AES-128,URI="{segment["key_local"]}"'
                    if segment['key'].get('iv'):declaration+=',IV='+segment['key']['iv']
                    lines.append(declaration)
                elif old_key:lines.append('#EXT-X-KEY:METHOD=NONE')
                old_key=current_key
            if segment['init_local']!=old_init:
                if segment['init_local']:lines.append(f'#EXT-X-MAP:URI="{segment["init_local"]}"')
                old_init=segment['init_local']
            lines.extend([f'#EXTINF:{segment["duration"]:.6f},',segment['local']])
        lines.append('#EXT-X-ENDLIST')
        playlist=folder/f'track-{index}.m3u8';playlist.write_text('\n'.join(lines)+'\n',encoding='utf-8')
        inputs.append((playlist,track['kind']))
    output=job.part.with_suffix('.merged.mp4');output.unlink(missing_ok=True)
    command=[executable,'-hide_banner','-nostdin','-loglevel','error','-y']
    for playlist,kind in inputs:
        command+=['-protocol_whitelist','file,crypto,data']
        if playlist.suffix=='.m3u8':command+=['-allowed_extensions','ALL']
        command+=['-i',str(playlist)]
    if len(inputs)>1:
        for index,(_,kind) in enumerate(inputs):command+=['-map',f'{index}:{"a" if kind=="audio" else "v"}:0']
    command+=['-c','copy','-movflags','+faststart','-f','mp4',str(output)]
    error_log=folder/'merge.log'
    with error_log.open('wb') as errors:
        process=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=errors,
                                 **({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
        try:
            deadline=time.monotonic()+180
            while process.poll() is None:
                job.check_stop()
                if output.exists() and job.exceeds_limit(output.stat().st_size):raise PreviewError('合并后的视频超过 500 MB，已停止保存。')
                if time.monotonic()>deadline:raise PreviewError('媒体合并超时，请检查 FFmpeg 或选择其他资源。')
                job.stop.wait(.1)
            if process.returncode or not output.is_file():
                errors.flush()
                reason=error_log.read_text(encoding='utf-8',errors='replace')[-500:]
                raise PreviewError('流媒体合并失败：'+reason)
            if output.stat().st_size<=0 or job.exceeds_limit(output.stat().st_size):raise PreviewError('合并后的视频超过 500 MB 或为空。')
            output.replace(job.part)
            job.downloaded=job.part.stat().st_size;job.asset['size']=job.downloaded
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=10)
            output.unlink(missing_ok=True)
            errors.close()
            error_log.unlink(missing_ok=True)
