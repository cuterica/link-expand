"""Optional public-link fallback used by fysh1010/bilibili-mcp.

Only a canonical public BV link goes to api.bugpk.com. Never send browser cookies,
custom request headers, tracking parameters, page text or images to the service.
"""
from collections import OrderedDict
import copy
import json
import re
import threading
import time
from urllib.parse import urlencode, urlsplit, parse_qs

from .metadata import PreviewError, clean_text, normalize_url, fetch_resource
from .media_resolver import imported_candidates

ENDPOINT='https://api.bugpk.com/api/bilibili'
CACHE=OrderedDict()
LOCK=threading.RLock()


def public_reference(url):
    url=normalize_url(url);parts=urlsplit(url);host=parts.hostname or ''
    if host!='bilibili.com' and not host.endswith('.bilibili.com'):return None
    match=re.fullmatch(r'/video/(BV[A-Za-z0-9]{10})/?',parts.path)
    if not match:return None
    query=parse_qs(parts.query);part=str(query.get('p',['1'])[0])
    if not part.isdigit() or not 1<=int(part)<=1000:raise PreviewError('B 站分 P 参数无效。')
    return 'https://www.bilibili.com/video/'+match[1]+'/' + ('?p='+str(int(part)) if int(part)!=1 else '')


def parse(url):
    source=public_reference(url)
    if not source:raise PreviewError('第三方解析仅支持 B 站公开视频 BV 链接。')
    with LOCK:
        cached=CACHE.get(source)
        if cached and time.monotonic()-cached[0]<90:return copy.deepcopy(cached[1])
    resource=fetch_resource(ENDPOINT+'?'+urlencode({'url':source}),2_000_000,timeout=15,
                            headers={'Accept':'application/json','User-Agent':'Mozilla/5.0'})
    try:response=json.loads(resource.body)
    except (ValueError,UnicodeError):raise PreviewError('B 站公开视频解析接口没有返回有效数据。') from None
    if not isinstance(response,dict):raise PreviewError('B 站公开视频解析接口响应格式无效。')
    data=response.get('data')
    if response.get('code')!=200 or not isinstance(data,dict):raise PreviewError('B 站公开视频解析暂时失败，请稍后重试。')
    title=clean_text(str(data.get('title','')),180)
    if not title:raise PreviewError('B 站公开视频解析未提供标题。')
    cover=str(data.get('cover') or '')
    if cover.startswith('http://') and (urlsplit(cover).hostname or '').endswith('.hdslb.com'):cover='https://'+cover[7:]
    preview={'url':source,'title':title,'description':clean_text(str(data.get('description','')),500),
             'image_url':cover,'site_name':'哔哩哔哩',
             'summary_source':'B 站公开链接解析','visual_source':'B 站视频封面'}
    candidates=[]
    videos=data.get('videos')
    items=videos if isinstance(videos,list) and videos else [{'url':data.get('url')}]
    for item in items[:64]:
        if not isinstance(item,dict) or not item.get('url'):continue
        candidates.append({'url':item['url'],'kind':'video','filename':title+'.mp4',
                           'quality':'完整视频','headers':{'Referer':source,'User-Agent':'Mozilla/5.0'}})
    catalog=imported_candidates(source,candidates) if candidates else None
    if catalog:catalog['title']=title
    result={'source':source,'preview':preview,'catalog':catalog,'method':'bilibili-public-parser'}
    with LOCK:
        CACHE[source]=(time.monotonic(),copy.deepcopy(result))
        while len(CACHE)>24:CACHE.popitem(last=False)
    return result
