// Executed in the page's main world; only metadata and media addresses are returned.
function linkExpandPage(play = false) {
  const meta = name => document.querySelector(`meta[property="${name}"],meta[name="${name}"]`)?.content || '';
  const text = value => (value || '').replace(/\s+/g, ' ').trim();
  let title = text(meta('og:title') || meta('twitter:title') || document.title).slice(0, 180);
  if (/出错啦.*bilibili|access denied|just a moment|安全验证|访问验证/i.test(title)) {
    return {error: '网页要求安全验证。请在此浏览器中正常打开链接、完成验证后，在软件重试。'};
  }
  let description = text(meta('og:description') || meta('twitter:description') || meta('description') ||
    [...document.querySelectorAll('article p,main p')].slice(0, 5).map(node => node.textContent).join(' ')).slice(0, 500);
  let image = meta('og:image:secure_url') || meta('og:image') || meta('twitter:image') || document.querySelector('video[poster]')?.poster || '';
  if (!image) {
    const images = [...document.images].filter(node => node.naturalWidth >= 280 && node.naturalHeight >= 140 && !/avatar|logo|icon/i.test(node.currentSrc || node.src));
    images.sort((a, b) => b.naturalWidth * b.naturalHeight - a.naturalWidth * a.naturalHeight);
    image = images[0]?.currentSrc || images[0]?.src || '';
  }
  if (image) try { image = new URL(image, location.href).href; } catch (_) { image = ''; }
  if (!/^https?:/.test(image)) image = '';
  const candidates = [];
  const add = (url, kind, extra = {}) => {
    if (/^https?:/.test(url || '')) candidates.push({url, kind, headers: {Referer: location.href}, ...extra});
  };
  for (const node of document.querySelectorAll('video,audio')) {
    add(node.currentSrc || node.src, node.tagName === 'AUDIO' ? 'audio' : 'video');
    for (const source of node.querySelectorAll('source')) add(source.src, node.tagName === 'AUDIO' ? 'audio' : 'video');
    if (play) { node.muted = true; node.play().catch(() => {}); }
  }
  // Bilibili exposes the same DASH resources its signed-in player uses.
  const dash = window.__playinfo__?.data?.dash;
  if (dash) {
    for (const kind of ['video', 'audio']) {
      const variants = [...(dash[kind] || [])].sort((a, b) => (b.bandwidth || 0) - (a.bandwidth || 0));
      if (variants[0]) add(variants[0].baseUrl || variants[0].base_url, kind, {mime: variants[0].mimeType || ''});
    }
  }
  const tiktokReference = /^(?:[\w-]+\.)*tiktok\.com$/i.test(location.hostname) && location.pathname.match(/\/@[^/]+\/video\/(\d{10,24})/);
  if (tiktokReference) {
    const id=tiktokReference[1];let item=null;
    try { const data=JSON.parse(document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__')?.textContent || '{}');item=data.__DEFAULT_SCOPE__?.['webapp.video-detail']?.itemInfo?.itemStruct; } catch (_) {}
    if (!item || String(item.id)!==id) try {item=JSON.parse(document.getElementById('SIGI_STATE')?.textContent || '{}').ItemModule?.[id];} catch (_) {}
    if (item && String(item.id)===id) {
      const caption=text(item.desc);const author=text(item.author?.nickname || item.author?.uniqueId || 'TikTok');
      title=text(caption ? caption+' | '+author : author+'的 TikTok 视频').slice(0,180);description=caption.slice(0,500);
      const video=item.video || {};image=video.originCover || video.cover || video.dynamicCover || image;
      const addVariant=(value,width,height,bitrate,frameRate)=>add(value,'video',{tiktok_id:id,width:Number(width)||0,height:Number(height)||0,bitrate:Number(bitrate)||0,frame_rate:frameRate || 0,quality:width&&height?width+'×'+height:'原始画质'});
      for (const variant of (video.bitrateInfo || video.bitRateInfo || []).slice(0,20)) {
        const address=variant.PlayAddr || {};for(const value of (address.UrlList || []).slice(0,4))addVariant(value,address.Width,address.Height,variant.Bitrate,variant.BitrateFPS);
      }
      for (const key of ['PlayAddrStruct','playAddr','downloadAddr']) {
        const address=video[key];if(typeof address==='string')addVariant(address,video.width,video.height,video.bitrate,0);
        else if(address)for(const value of (address.UrlList || []).slice(0,4))addVariant(value,address.Width || video.width,address.Height || video.height,video.bitrate,0);
      }
      candidates.sort((a,b)=>(b.width||0)*(b.height||0)-(a.width||0)*(a.height||0)||(b.bitrate||0)-(a.bitrate||0));
    }
  }
  const douyinReference=/^(?:[\w-]+\.)*douyin\.com$/i.test(location.hostname) && (location.pathname.match(/\/video\/(\d{10,24})/)?.[1] || new URL(location.href).searchParams.get('modal_id'));
  if(douyinReference){
    let root=null;try{root=JSON.parse(decodeURIComponent(document.getElementById('RENDER_DATA')?.textContent || '{}'));}catch(_){}
    const find=(value,depth=0)=>{if(depth>16||!value||typeof value!=='object')return null;if(String(value.awemeId || value.aweme_id)===douyinReference&&value.video)return value;for(const child of Object.values(value).slice(0,200)){const item=find(child,depth+1);if(item)return item;}return null;};
    const item=find(root || window._ROUTER_DATA);
    if(item){
      description=text(item.desc || item.itemTitle).slice(0,500);title=(description || '抖音视频').slice(0,180);
      const video=item.video || {};image=video.originCover || video.cover || image;
      const urls=value=>typeof value==='string'?[value]:Array.isArray(value)?value.slice(0,4).map(item=>typeof item==='string'?item:item.src):value?.urlList || value?.url_list || [];
      for(const variant of (video.bitRateList || video.bit_rate || []).slice(0,40)){
        if(variant.audioFileId)continue;
        for(const address of urls(variant.playAddr || variant.play_addr))add(address,'video',{douyin_id:douyinReference,width:Number(variant.width)||0,height:Number(variant.height)||0,bitrate:Number(variant.bitRate || variant.bit_rate)||0,frame_rate:variant.fps || 0,quality:variant.width+'×'+variant.height});
      }
      candidates.sort((a,b)=>(b.width||0)*(b.height||0)-(a.width||0)*(a.height||0)||(b.bitrate||0)-(a.bitrate||0));
    }
  }
  let imageData = '';
  if (image) {
    const node = [...document.images].find(node => (node.currentSrc || node.src) === image && node.naturalWidth);
    if (node) try {
      const canvas = document.createElement('canvas');
      canvas.width = Math.min(960, node.naturalWidth); canvas.height = Math.round(canvas.width * node.naturalHeight / node.naturalWidth);
      canvas.getContext('2d').drawImage(node, 0, 0, canvas.width, canvas.height);
      imageData = canvas.toDataURL('image/jpeg', 0.85);
    } catch (_) {}
  }
  if (!imageData) {
    const video = [...document.querySelectorAll('video')].find(node => node.videoWidth && node.readyState >= 2);
    if (video) try {
      const canvas = document.createElement('canvas');
      canvas.width = Math.min(960, video.videoWidth); canvas.height = Math.round(canvas.width * video.videoHeight / video.videoWidth);
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      imageData = canvas.toDataURL('image/jpeg', 0.8);
    } catch (_) {}
  }
  return {preview: {url: location.href, title, description, image_url: image, site_name: text(meta('og:site_name')),
    ...(imageData ? {image_data: imageData, visual_source: image ? '浏览器封面' : '浏览器视频画面'} : {})}, candidates};
}
