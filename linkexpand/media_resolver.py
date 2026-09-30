"""Generic file/media discovery from direct URLs, HTML and captured requests."""

from __future__ import annotations

import hashlib
from html import unescape
from html.parser import HTMLParser
import json
import mimetypes
from pathlib import PurePosixPath
import re
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

from .download_http import resource_url, checked_headers, scoped_headers, public_headers,document_body
from .metadata import PreviewError, clean_text
from .video_downloads import open_video
from .quality import quality_rank

FILE_EXTENSIONS = {'zip','7z','rar','tar','gz','pdf','exe','msi','iso','dmg','bin','txt','csv','json',
                   'jpg','jpeg','png','gif','webp','mp3','m4a','aac','wav','flac','ogg','mp4','webm','mkv','mov','m4v','avi','ts'}
VIDEO_EXTENSIONS = {'mp4','webm','mkv','mov','m4v','avi','ts'}
AUDIO_EXTENSIONS = {'mp3','m4a','aac','wav','flac','ogg'}


def classify(url, mime=''):
    suffix = PurePosixPath(urlsplit(url).path).suffix.lower().lstrip('.')
    mime = mime.split(';',1)[0].strip().lower()
    if suffix == 'm3u8' or mime in {'application/vnd.apple.mpegurl','application/x-mpegurl','audio/mpegurl','audio/x-mpegurl'}:
        return 'hls'
    if suffix == 'mpd' or mime == 'application/dash+xml':
        return 'dash'
    if suffix in VIDEO_EXTENSIONS or mime.startswith('video/'):
        return 'video'
    if suffix in AUDIO_EXTENSIONS or mime.startswith('audio/'):
        return 'audio'
    if suffix in FILE_EXTENSIONS or (mime and mime not in {'text/html','application/xhtml+xml'}):
        return 'file'
    return None


def safe_filename(value, fallback='download'):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', unquote(value)).strip(' .')[:180]
    if not name or name.split('.',1)[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:
        name = fallback
    return name


def filename_from_headers(url, headers):
    disposition = headers.get('content-disposition','')
    encoded = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.I)
    basic = re.search(r'filename\s*=\s*(?:"([^"]*)"|([^;]+))', disposition, re.I)
    value = unquote(encoded[1]) if encoded else (basic[1] or basic[2]).strip() if basic else unquote(PurePosixPath(urlsplit(url).path).name)
    name=safe_filename(value or 'download')
    if not PurePosixPath(name).suffix:
        name+=mimetypes.guess_extension(headers.get('content-type','').split(';',1)[0]) or ''
    return name


def inspect_resource(url, headers=None, credential_origin=None, max_bytes=2*1024*1024):
    with open_video(url, {'Range':f'bytes=0-{max_bytes-1}'}, headers, credential_origin,allow_compressed=True) as response:
        info = {key.lower():value for key,value in response.getheaders()}
        mime = info.get('content-type','')
        disposition = info.get('content-disposition','')
        kind = classify(response.resource_url,mime)
        is_document = mime.split(';',1)[0].lower() in {'text/html','application/xhtml+xml'}
        body = b''
        if is_document or kind in {'hls','dash'} or (not kind and not disposition):
            body = document_body(response,max_bytes)
            if body.lstrip().startswith(b'#EXTM3U'):kind='hls'
            elif re.search(br'<(?:[\w-]+:)?MPD\b',body[:2048]):kind='dash'
        if disposition and 'attachment' in disposition.lower():kind='file'
        return {'url':response.resource_url,'kind':None if is_document else kind,
                'mime':mime,'headers':info,'body':body,'filename':filename_from_headers(response.resource_url,info)}


def full_media_url(url):
    # A numeric query range represents a player's partial request, not a whole file.
    parts=urlsplit(resource_url(url))
    query=[(key,value) for key,value in parse_qsl(parts.query,keep_blank_values=True)
           if not (key.lower()=='range' and re.fullmatch(r'\d+-\d+',value))]
    return urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),'')) if len(query)!=len(parse_qsl(parts.query,keep_blank_values=True)) else url


class MediaHTML(HTMLParser):
    def __init__(self,base):
        super().__init__(convert_charrefs=True)
        self.base=base;self.candidates=[];self.title=[];self.in_title=False;self.scripts=[];self.in_script=False;self.script=[]
    def add(self,url,kind=None,mime=''):
        if not url:return
        try:
            resolved=resource_url(urljoin(self.base,unescape(url)))
        except PreviewError:return
        self.candidates.append({'url':resolved,'kind':kind or classify(resolved,mime) or 'video','mime':mime})
    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if tag=='base' and values.get('href'):self.base=urljoin(self.base,values['href'])
        if tag in {'video','audio','source'}:self.add(values.get('src') or values.get('data-src'),classify(values.get('src') or '',values.get('type') or '') or ('audio' if tag=='audio' else 'video'),values.get('type') or '')
        if tag=='a' and values.get('href') and classify(values['href']):self.add(values['href'])
        if tag=='meta' and (values.get('property') or values.get('name')) in {'og:video','og:video:url','og:video:secure_url','twitter:player:stream'}:self.add(values.get('content'))
        if tag=='title':self.in_title=True
        if tag=='script':self.in_script=True;self.script=[]
    def handle_data(self,data):
        if self.in_title:self.title.append(data)
        if self.in_script:self.script.append(data)
    def handle_endtag(self,tag):
        if tag=='title':self.in_title=False
        if tag=='script':self.scripts.append(''.join(self.script));self.in_script=False
    def extract_scripts(self):
        for script in self.scripts:
            if len(script)>1024*1024:continue
            try:
                data=json.loads(script)
                def visit(value,depth=0):
                    if depth>10:return
                    if isinstance(value,dict):
                        for key,item in value.items():
                            if key in {'contentUrl','contentURL','file','src','url','playbackUrl','manifestUrl'} and isinstance(item,str) and (classify(item) or key.lower()=='contenturl'):self.add(item)
                            else:visit(item,depth+1)
                    elif isinstance(value,list):
                        for item in value[:100]:visit(item,depth+1)
                visit(data)
            except (ValueError,RecursionError):pass
            text=script.replace('\\/','/')
            for match in re.findall(r'https?://[^\s"\'<>\\]+\.(?:m3u8|mpd|mp4|webm|m4a)(?:\?[^\s"\'<>\\]*)?',text,re.I):self.add(match)


def candidate_resource(candidate,index,source,headers,credential_origin):
    url=full_media_url(candidate['url'])
    kind=candidate.get('kind') or classify(url) or 'file'
    filename=safe_filename(candidate.get('filename') or filename_from_headers(url,{'content-type':candidate.get('mime','')}))
    if kind in {'hls','dash','pair'}:
        filename=str(PurePosixPath(filename).with_suffix('.mp4'))
    elif not PurePosixPath(filename).suffix:
        filename+= {'video':'.mp4','audio':'.m4a','hls':'.mp4','dash':'.mp4'}.get(kind,'.bin')
    return {'index':index,'post_id':hashlib.sha256(url.encode()).hexdigest()[:20],
            'author':urlsplit(source).hostname,'duration_ms':0,'kind':kind,'filename':filename,
            'credential_origin':credential_origin,'variants':[{'url':url,'kind':kind,'quality':candidate.get('quality') or kind.upper(),
                'headers':scoped_headers(checked_headers(candidate.get('headers') or headers),url,credential_origin)}]}


def resolve(url, headers=None, scan=False):
    url=resource_url(url);headers=checked_headers(headers)
    from .xmedia import post_reference,preview_for_post,X_HOSTS
    parts=urlsplit(url)
    is_x=parts.hostname in X_HOSTS and parts.port in {None,80,443}
    if is_x and post_reference(url) and not headers:
        preview=preview_for_post(url)
        if preview.videos:
            for video in preview.videos:
                video['kind']='video'
                for variant in video['variants']:variant['headers']={'Referer':'https://x.com/'}
            return {'source':url,'title':preview.title,'resources':preview.videos}
    from .tiktok import post_reference as tiktok_reference, parse_resource
    if tiktok_reference(url):
        from .metadata import fetch_resource,MAX_IMAGE
        preview=parse_resource(fetch_resource(url,MAX_IMAGE,headers=headers))
        if preview.videos:return {'source':preview.url,'title':preview.title,'resources':preview.videos}
    inspected=inspect_resource(url,headers,url)
    source=inspected['url']
    if inspected['kind']:
        candidates=[{'url':source,'kind':inspected['kind'],'filename':inspected['filename']}]
        title=inspected['filename']
    else:
        parser=MediaHTML(source)
        parser.feed(inspected['body'].decode('utf-8',errors='replace'));parser.extract_scripts()
        candidates=parser.candidates;title=clean_text(''.join(parser.title),180) or urlsplit(source).hostname
        headers.setdefault('Referer',source)
    if scan:
        from .media_scan import scan_page
        candidates.extend(scan_page(source,headers))
    seen=set();resources=[]
    for candidate in candidates:
        try:
            normalized=full_media_url(resource_url(candidate['url']))
            if normalized in seen:continue
            seen.add(normalized)
            resources.append(candidate_resource(dict(candidate,url=normalized),len(resources)+1,source,headers,url))
            if len(resources)>=64:break
        except PreviewError:continue
    if not resources:
        raise PreviewError('未找到直接可下载的资源。可以尝试动态网页识别，或用浏览器捕获扩展取得实际媒体请求。')
    if inspected['kind']=='hls' and inspected['body'] and resources:
        from .streaming import hls_variants
        variants=hls_variants(inspected['body'].decode('utf-8-sig',errors='replace'),source)
        if variants:
            for variant in variants:variant['headers']=scoped_headers(headers,variant['url'],url)
            resources[0]['variants']=variants
    elif inspected['kind']=='dash' and inspected['body'] and resources:
        import xml.etree.ElementTree as ET
        from .streaming import children
        text=inspected['body'].decode('utf-8-sig',errors='replace')
        if not re.search(r'<!DOCTYPE|<!ENTITY',text,re.I):
            try:
                root=ET.fromstring(text);variants=[]
                for period in children(root,'Period')[:1]:
                    for adaptation in children(period,'AdaptationSet'):
                        if adaptation.get('contentType')!='video' and not adaptation.get('mimeType','').startswith('video/'):continue
                        for representation in children(adaptation,'Representation'):
                            variants.append({'url':source,'kind':'dash','dash_video_id':representation.get('id'),
                                'bandwidth':int(representation.get('bandwidth','0')),
                                'quality':f'{representation.get("width",adaptation.get("width","?"))}×{representation.get("height",adaptation.get("height","?"))}',
                                'frame_rate':representation.get('frameRate',adaptation.get('frameRate')),
                                'headers':scoped_headers(headers,source,url)})
                if variants:resources[0]['variants']=sorted(variants,key=quality_rank,reverse=True)
            except (ET.ParseError,ValueError):pass
    return {'source':source,'title':title,'resources':resources}


def imported_candidates(source,candidates):
    source=resource_url(source)
    from .tiktok import browser_video
    tiktok=browser_video(source,candidates)
    if tiktok:return {'source':source,'title':'TikTok 视频','resources':[tiktok]}
    resources=[];seen=set()
    for item in candidates[:64]:
        if not isinstance(item,dict):continue
        url=full_media_url(resource_url(item.get('url','')))
        if url in seen:continue
        seen.add(url)
        headers=checked_headers(item.get('headers'))
        headers.setdefault('Referer',source)
        resources.append(candidate_resource(item,len(resources)+1,source,headers,url))
    if not resources:raise PreviewError('捕获列表没有有效的下载地址。')
    video=[item for item in resources if item.get('kind')=='video']
    audio=[item for item in resources if item.get('kind')=='audio']
    if len(video)==1 and len(audio)==1:
        v,a=video[0]['variants'][0],audio[0]['variants'][0]
        pair={'index':len(resources)+1,'post_id':hashlib.sha256((v['url']+a['url']).encode()).hexdigest()[:20],
              'author':urlsplit(source).hostname,'duration_ms':0,'kind':'pair','filename':'merged.mp4',
              'credential_origin':v['url'],'variants':[{'url':v['url'],'audio_url':a['url'],'kind':'pair',
                'quality':'视频 + 音频合并','headers':v.get('headers',{}),'audio_headers':a.get('headers',{}),
                'audio_origin':a['url']}]}
        resources.insert(0,pair)
    return {'source':source,'title':'浏览器捕获的资源','resources':resources}
