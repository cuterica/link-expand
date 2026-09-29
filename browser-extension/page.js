// Executed in the page's main world; only metadata and media addresses are returned.
function linkExpandPage(play = false) {
  const meta = name => document.querySelector(`meta[property="${name}"],meta[name="${name}"]`)?.content || '';
  const text = value => (value || '').replace(/\s+/g, ' ').trim();
  const title = text(meta('og:title') || meta('twitter:title') || document.title).slice(0, 180);
  if (/出错啦.*bilibili|access denied|just a moment|安全验证|访问验证/i.test(title)) {
    return {error: '网页要求安全验证。请在此浏览器中正常打开链接、完成验证后，在软件重试。'};
  }
  const description = text(meta('og:description') || meta('twitter:description') || meta('description') ||
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
  if (!image) {
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
