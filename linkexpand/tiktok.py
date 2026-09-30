"""Read the requested TikTok item's public hydration data, including complete media."""

from html.parser import HTMLParser
import json
import re
from urllib.parse import urlsplit

from .metadata import Preview, PreviewError, clean_text, concise_summary, decode_html, normalize_url
from .download_http import resource_url, checked_headers
from .quality import quality_rank


def post_reference(url):
    parts=urlsplit(normalize_url(url))
    host=parts.hostname or ''
    if host!='tiktok.com' and not host.endswith('.tiktok.com'):return None
    match=re.fullmatch(r'/@([^/]+)/video/(\d{10,24})/?',parts.path)
    return {'author':match[1],'id':match[2]} if match else None


def media_host(url):
    host=urlsplit(url).hostname or ''
    return any(host==domain or host.endswith('.'+domain) for domain in ['tiktok.com','tiktokcdn.com','tiktokcdn-us.com','tiktokv.com','muscdn.com','ibytedtos.com','byteoversea.com'])


class Hydration(HTMLParser):
    def __init__(self):
        super().__init__();self.current=None;self.parts=[];self.documents=[]
    def handle_starttag(self,tag,attrs):
        if tag=='script' and dict(attrs).get('id') in {'__UNIVERSAL_DATA_FOR_REHYDRATION__','SIGI_STATE'}:
            self.current=dict(attrs)['id'];self.parts=[]
    def handle_data(self,value):
        if self.current:self.parts.append(value)
    def handle_endtag(self,tag):
        if tag=='script' and self.current:
            try:self.documents.append((self.current,json.loads(''.join(self.parts))))
            except ValueError:pass
            self.current=None;self.parts=[]


def parse_resource(resource):
    reference=post_reference(resource.url)
    if not reference:raise PreviewError('这不是 TikTok 视频页面。')
    parser=Hydration();parser.feed(decode_html(resource))
    item=None
    for name,data in parser.documents:
        if not isinstance(data,dict):continue
        if name=='__UNIVERSAL_DATA_FOR_REHYDRATION__':
            detail=(data.get('__DEFAULT_SCOPE__') or {}).get('webapp.video-detail') or {}
            candidate=(detail.get('itemInfo') or {}).get('itemStruct')
        else:candidate=(data.get('ItemModule') or {}).get(reference['id'])
        if isinstance(candidate,dict) and str(candidate.get('id'))==reference['id']:
            item=candidate;break
    if item is None:raise PreviewError('TikTok 页面尚未提供这条视频的数据，正在尝试真实浏览器读取。')
    caption=clean_text(str(item.get('desc') or ''),500)
    author=item.get('author') or {}
    handle=clean_text(str(author.get('uniqueId') or reference['author']),80)
    name=clean_text(str(author.get('nickname') or handle),80)
    preview=Preview(resource.url,clean_text((caption+' | '+name) if caption else name+'的 TikTok 视频',180),
                    concise_summary(caption),'tiktok.com','TikTok',summary_source='TikTok 视频说明')
    video=item.get('video') or {}
    for key in ['originCover','cover','dynamicCover']:
        address=video.get(key)
        if isinstance(address,str) and address:
            try:address=resource_url(address)
            except PreviewError:continue
            preview.candidates.append({'url':address,'source':'TikTok 视频封面','priority':1100 if key=='originCover' else 1050})
    if preview.candidates:preview.image_url=preview.candidates[0]['url']
    variants=[];seen=set()
    def add(address,width=0,height=0,bitrate=0,fps=0):
        if not isinstance(address,str):return
        try:address=resource_url(address)
        except PreviewError:return
        if address in seen or not media_host(address):return
        seen.add(address)
        variants.append({'url':address,'kind':'video','width':width,'height':height,'bitrate':bitrate,'frame_rate':fps,
            'quality':f'{width}×{height}' if width and height else '原始画质',
            'headers':{'Referer':resource.url,'User-Agent':'Mozilla/5.0'},'credential_origin':address})
    for variant in (video.get('bitrateInfo') or video.get('bitRateInfo') or [])[:20]:
        address=variant.get('PlayAddr') or {}
        for url in (address.get('UrlList') or [])[:4]:
            add(url,int(address.get('Width') or 0),int(address.get('Height') or 0),int(variant.get('Bitrate') or 0),variant.get('BitrateFPS') or 0)
    for key in ['PlayAddrStruct','playAddr','downloadAddr']:
        value=video.get(key)
        if isinstance(value,dict):
            for address in (value.get('UrlList') or [])[:4]:add(address,int(value.get('Width') or video.get('width') or 0),int(value.get('Height') or video.get('height') or 0),int(video.get('bitrate') or 0))
        elif isinstance(value,str):add(value,int(video.get('width') or 0),int(video.get('height') or 0),int(video.get('bitrate') or 0))
    variants.sort(key=quality_rank,reverse=True)
    if variants:
        preview.videos=[{'index':1,'post_id':reference['id'],'author':handle,'duration_ms':int(float(video.get('duration') or 0)*1000),
            'kind':'video','filename':f'{handle}_{caption or reference["id"]}.mp4','variants':variants[:64]}]
        preview.selected_video=1
    else:preview.warnings.append('TikTok 页面没有提供这条视频的播放地址。')
    return preview


def browser_video(source,candidates,title='TikTok 视频'):
    reference=post_reference(source)
    if not reference:return None
    variants=[]
    for candidate in candidates:
        if not isinstance(candidate,dict):continue
        if candidate.get('tiktok_id')!=reference['id'] or candidate.get('kind')!='video':continue
        address=resource_url(candidate['url'])
        if not media_host(address):continue
        variants.append({key:candidate[key] for key in ['width','height','bitrate','frame_rate','quality'] if key in candidate} |
                        {'url':address,'kind':'video','headers':checked_headers(candidate.get('headers')),'credential_origin':address})
    if not variants:return None
    variants.sort(key=quality_rank,reverse=True)
    return {'index':1,'post_id':reference['id'],'author':reference['author'],'duration_ms':0,'kind':'video',
            'filename':title+'.mp4','variants':variants[:64]}
