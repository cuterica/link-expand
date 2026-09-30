"""Read only the requested Douyin modal/video from its public rendered data."""
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlsplit,parse_qs,unquote

from .metadata import Preview,PreviewError,normalize_url,clean_text,concise_summary,decode_html
from .download_http import resource_url,checked_headers
from .quality import quality_rank


def post_reference(url):
    parts=urlsplit(normalize_url(url));host=parts.hostname or ''
    if host!='douyin.com' and not host.endswith('.douyin.com'):return None
    match=re.fullmatch(r'/video/(\d{10,24})/?',parts.path)
    identifier=match[1] if match else parse_qs(parts.query).get('modal_id',[''])[0]
    return {'id':identifier} if re.fullmatch(r'\d{10,24}',identifier) else None


def media_host(url):
    host=urlsplit(url).hostname or ''
    return any(host==domain or host.endswith('.'+domain) for domain in ['douyin.com','douyinvod.com','zjcdn.com','amemv.com','bytecdn.cn','douyinstatic.com','douyinpic.com','byteimg.com'])


class RenderData(HTMLParser):
    def __init__(self):super().__init__();self.inside=False;self.parts=[];self.data=None
    def handle_starttag(self,tag,attrs):
        if tag=='script' and dict(attrs).get('id')=='RENDER_DATA':self.inside=True
    def handle_data(self,text):
        if self.inside:self.parts.append(text)
    def handle_endtag(self,tag):
        if tag=='script' and self.inside:
            try:self.data=json.loads(unquote(''.join(self.parts)))
            except ValueError:pass
            self.inside=False


def find_item(value,identifier,depth=0):
    if depth>16:return None
    if isinstance(value,dict):
        if str(value.get('awemeId',value.get('aweme_id','')))==identifier and isinstance(value.get('video'),dict):return value
        for child in value.values():
            item=find_item(child,identifier,depth+1)
            if item:return item
    elif isinstance(value,list):
        for child in value[:200]:
            item=find_item(child,identifier,depth+1)
            if item:return item
    return None


def addresses(value):
    if isinstance(value,str):return [value]
    if isinstance(value,dict):return addresses(value.get('urlList',value.get('url_list',[])))
    if isinstance(value,list):return [item if isinstance(item,str) else item.get('src','') for item in value[:4] if isinstance(item,(str,dict))]
    return []


def parse_resource(resource):
    reference=post_reference(resource.url)
    if not reference:raise PreviewError('这不是抖音视频链接。')
    parser=RenderData();parser.feed(decode_html(resource));item=find_item(parser.data,reference['id'])
    if not item:raise PreviewError('抖音页面尚未提供目标视频资料，正在尝试真实浏览器读取。')
    caption=clean_text(str(item.get('desc') or item.get('itemTitle') or ''),500)
    author=item.get('authorInfo') or item.get('author') or {}
    name=clean_text(str(author.get('nickname') or '抖音用户'),80)
    preview=Preview(resource.url,clean_text(caption or name+'的抖音视频',180),concise_summary(caption),'douyin.com','抖音',summary_source='抖音视频说明')
    video=item['video']
    for key in ['originCover','origin_cover','cover','dynamicCover']:
        for address in addresses(video.get(key)):
            try:address=resource_url(address)
            except PreviewError:continue
            preview.candidates.append({'url':address,'source':'抖音视频封面','priority':1100 if key in {'originCover','origin_cover'} else 1050})
    if preview.candidates:preview.image_url=preview.candidates[0]['url']
    variants=[];seen=set()
    for value in (video.get('bitRateList') or video.get('bit_rate') or [])[:40]:
        if not isinstance(value,dict) or value.get('audioFileId'):continue
        width=int(value.get('width') or 0);height=int(value.get('height') or 0)
        for address in addresses(value.get('playAddr',value.get('play_addr'))):
            try:address=resource_url(address)
            except PreviewError:continue
            if address in seen or not media_host(address):continue
            seen.add(address);variants.append({'url':address,'kind':'video','width':width,'height':height,'quality':f'{width}×{height}',
                'bitrate':int(value.get('bitRate',value.get('bit_rate',0)) or 0),'frame_rate':value.get('fps') or 0,
                'headers':{'Referer':resource.url,'User-Agent':'Mozilla/5.0'},'credential_origin':address})
    if not variants:
        for address in addresses(video.get('playAddr',video.get('play_addr'))):
            try:address=resource_url(address)
            except PreviewError:continue
            if media_host(address):variants.append({'url':address,'kind':'video','quality':'原始画质','headers':{'Referer':resource.url},'credential_origin':address})
    variants.sort(key=quality_rank,reverse=True)
    if variants:
        preview.videos=[{'index':1,'post_id':reference['id'],'author':name,'duration_ms':int(video.get('duration') or 0),'kind':'video','filename':(caption or reference['id'])+'.mp4','variants':variants[:64]}]
        preview.selected_video=1
    return preview


def browser_video(source,candidates,title='抖音视频'):
    reference=post_reference(source)
    if not reference:return None
    variants=[]
    for candidate in candidates:
        if not isinstance(candidate,dict) or candidate.get('douyin_id')!=reference['id'] or candidate.get('kind')!='video':continue
        address=resource_url(candidate['url'])
        if not media_host(address):continue
        variants.append({key:candidate[key] for key in ['width','height','bitrate','frame_rate','quality'] if key in candidate} |
                        {'url':address,'kind':'video','headers':checked_headers(candidate.get('headers')),'credential_origin':address})
    if not variants:return None
    variants.sort(key=quality_rank,reverse=True)
    return {'index':1,'post_id':reference['id'],'author':'抖音用户','duration_ms':0,'kind':'video','filename':title+'.mp4','variants':variants[:64]}
