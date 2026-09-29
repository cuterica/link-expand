'use strict';
const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="local-token"]').content;
const expectedVersion = document.querySelector('meta[name="app-version"]').content;
let current = null;
let busy = false;
let cardBlob = null;
let toastTimer;
let expandTimer;
let lastRequested = '';
let revision = 0;
let requestController = null;
let nativeRich = false;
let clipboardReady = false;
let canCopyVideo = false;
let videoJob = null;
let videoJobId = null;
let videoPolling = false;
let downloadCatalog = null;
let browserPairingKey = '';
const actionIds = ['copy-image', 'copy-text', 'download', 'download-cover'];
try { $('bili-parser').checked = localStorage.getItem('bili-parser') !== 'false'; } catch (_) {}
$('bili-parser').addEventListener('change',()=>{try {localStorage.setItem('bili-parser',String($('bili-parser').checked));}catch(_){} lastRequested='';});

async function api(path, data, signal) {
  const response = await fetch(path, {method: 'POST', signal,
    headers: {'X-Local-Token': token, 'Content-Type': 'application/json'}, body: JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '操作失败，请重试。');
  return result;
}

async function browserStatus() {
  const response = await fetch('/api/browser/status', {headers: {'X-Local-Token': token}});
  if (!response.ok) return {connected: false, browsers: []};
  return response.json();
}
async function browserExpand(url, signal) {
  const job = await api('/api/browser/request', {url,bili_parser:$('bili-parser').checked}, signal);
  const cancel = () => { void api('/api/browser/cancel', {id: job.id}).catch(() => {}); };
  signal?.addEventListener('abort', cancel, {once: true});
  try {
    if (signal?.aborted) { cancel(); throw new DOMException('Cancelled', 'AbortError'); }
    while (true) {
      const response = await fetch('/api/browser/jobs/' + job.id, {signal, headers: {'X-Local-Token': token}});
      const state = await response.json();
      if (!response.ok || state.status === 'error') throw new Error(state.error || '浏览器读取失败。');
      if (state.status === 'cancelled') throw new DOMException('Cancelled', 'AbortError');
      if (state.status === 'complete') return state.result;
      if (state.status === 'fallback') feedback('常规读取失败，正在尝试备用解析；必要时会用 Playwright 打开真实浏览器窗口…');
      await new Promise(resolve => setTimeout(resolve, 700));
    }
  } finally { signal?.removeEventListener('abort', cancel); }
}

function feedback(message, error = false) {
  $('feedback').textContent = message;
  $('feedback').classList.toggle('error', error);
  $('feedback').hidden = !message;
}

function toast(message) {
  clearTimeout(toastTimer);
  $('toast').querySelector('span').textContent = message;
  $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4500);
}

function setBusy(value) {
  busy = value;
  $('generate').classList.toggle('loading', value);
  $('generate').querySelector('span').textContent = value ? '正在展开，请稍候…' : '展开链接';
  $('save-edit').disabled = value;
  actionIds.forEach(id => { $(id).disabled = value || !current; });
  $('copy-text').disabled = value || !current || !clipboardReady;
  if (!value) $('download-cover').disabled = !current?.cover;
}

async function display(preview, job) {
  if (job !== revision) return;
  current = preview;
  downloadCatalog = null;
  $('download-url').value = preview.url;
  displayVideoChoices(preview);
  cardBlob = null;
  $('card-site').textContent = preview.site_name;
  $('card-title').textContent = preview.title;
  $('card-description').textContent = preview.description;
  $('card-description').hidden = !preview.description;
  $('card-url').replaceChildren(document.createTextNode(preview.domain));
  $('card-url').href = preview.url;
  $('sample-cover').hidden = true;
  $('cover-container').hidden = !preview.cover;
  $('cover-image').hidden = !preview.cover;
  if (preview.cover) $('cover-image').src = preview.cover;
  else $('cover-image').removeAttribute('src');
  $('preview-badge').textContent = preview.visual_source || '文字预览';
  $('visual-source').textContent = preview.visual_source ? '画面来源 · ' + preview.visual_source : '暂无可用图片';
  $('summary-source').textContent = '摘要来源 · ' + preview.summary_source;
  $('message-time').textContent = new Date().toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'});
  $('url-input').value = preview.url;
  lastRequested = preview.url;
  $('title-input').value = preview.title;
  $('description-input').value = preview.description;
  $('edit-hint').textContent = '可以修改标题与摘要，调整后重新生成卡片。';
  feedback(preview.warnings.join(' '));
  try {
    const response = await fetch(preview.image);
    if (!response.ok) throw new Error('卡片图片加载失败，请重新展开。');
    const blob = await response.blob();
    if (job === revision) cardBlob = blob;
  } catch (error) { if (job === revision) feedback(error.message, true); }
  if (job === revision) setBusy(false);
}

async function expand(force = false) {
  const url = $('url-input').value.trim();
  if (!url || (!force && url === lastRequested)) return;
  clearTimeout(expandTimer);
  requestController?.abort();
  requestController = new AbortController();
  const job = ++revision;
  lastRequested = url;
  setBusy(true);
  feedback('正在读取摘要与图片；没有合适图片时，会截取视频画面或网页。');
  const signal = requestController.signal;
  try {
    const status = await browserStatus();
    if (signal.aborted) return;
    if (status.connected) {
      feedback(`正在通过已配对的 ${status.browsers.join(' / ')} 读取网页和媒体…`);
      const result = await browserExpand(url, signal);
      await display(result.preview, job);
      if (job === revision && result.catalog) displayCatalog(result.catalog);
    } else {
      const preview=await api('/api/preview', {url,bili_parser:$('bili-parser').checked}, signal);
      await display(preview, job);
      if (job === revision && preview.catalog) displayCatalog(preview.catalog);
    }
  }
  catch (error) {
    if (job !== revision || error.name === 'AbortError') return;
    feedback(error.message, true);
    lastRequested = '';
    setBusy(false);
  }
}

$('url-form').addEventListener('submit', event => { event.preventDefault(); expand(true); });
$('url-input').addEventListener('input', () => {
  clearTimeout(expandTimer);
  // Invalidate the old result immediately, so it cannot overwrite a newly typed URL.
  if (busy) { ++revision; requestController?.abort(); setBusy(false); }
  const value = $('url-input').value.trim();
  if (/^(https?:\/\/\S+|[\w.-]+\.[a-z]{2,}(?:\/\S*)?)$/i.test(value)) {
    expandTimer = setTimeout(() => expand(), 900);
  }
});
$('url-input').addEventListener('paste', () => {
  clearTimeout(expandTimer);
  expandTimer = setTimeout(() => expand(), 50);
});

$('edit-toggle').addEventListener('click', () => {
  const show = $('editor').hidden;
  $('editor').hidden = !show;
  $('edit-toggle').setAttribute('aria-expanded', String(show));
  $('edit-toggle').querySelector('span').textContent = show ? '收起编辑' : '手动编辑';
  if (show) $('title-input').focus();
});

$('editor').addEventListener('submit', async event => {
  event.preventDefault();
  clearTimeout(expandTimer);
  if (busy) return;
  if (!$('url-input').value.trim()) { feedback('请先填写网页地址。', true); $('url-input').focus(); return; }
  const job = ++revision;
  setBusy(true);
  try {
    const sameURL = current && $('url-input').value.trim() === current.url;
    const data = {url: $('url-input').value.trim(), title: $('title-input').value,
      description: $('description-input').value, ...(sameURL ? {id: current.id, site_name: current.site_name} : {})};
    await display(await api(sameURL ? '/api/edit' : '/api/manual', data), job);
    toast('卡片内容已更新');
  } catch (error) { feedback(error.message, true); setBusy(false); }
});

async function writeCardImage() {
  if (!navigator.clipboard?.write || !window.ClipboardItem) throw new Error('浏览器不支持复制图片，请下载 PNG。');
  if (!cardBlob) throw new Error('卡片尚未加载，请稍后重试或下载 PNG。');
  await navigator.clipboard.write([new ClipboardItem({'image/png': cardBlob})]);
}

$('copy-image').addEventListener('click', async () => {
  if (!current || busy) return;
  try {
    await writeCardImage();
    toast('卡片图片已复制');
  } catch (error) { feedback('复制失败：' + error.message, true); }
});

$('copy-text').addEventListener('click', async () => {
  if (!current || busy || !clipboardReady) return;
  try {
    if (nativeRich) {
      await api('/api/copy-rich', {id: current.id});
      toast('图片、文字与真实链接已一起复制，可以粘贴到微信 / QQ');
      return;
    }
    if (!navigator.clipboard?.write || !window.ClipboardItem) throw new Error('浏览器不支持复制图文，请换用 Chrome 或 Edge。');
    await navigator.clipboard.write([new ClipboardItem({
      'text/html': new Blob([current.html], {type: 'text/html'}),
      'text/plain': new Blob([current.text], {type: 'text/plain'})
    })]);
    toast('图文与可点击链接已复制，粘贴到支持富文本的编辑器即可');
  } catch (error) { feedback('复制失败：' + error.message, true); }
});

function download(url, filename) {
  if (!current || busy || !url) return;
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
}
$('download').addEventListener('click', () => download(current?.image, 'link-preview.png'));
$('download-cover').addEventListener('click', () => download(current?.visual, 'link-image.png'));

fetch('/api/capabilities', {headers: {'X-Local-Token': token}})
  .then(response => {
    if (response.status === 404) throw new Error('当前连接的是旧后台，图文复制没有启用。请关闭旧程序并重新启动新版。');
    if (!response.ok) throw new Error('后台已切换，请刷新页面重新连接。');
    return response.json();
  })
  .then(capabilities => {
    if (capabilities.version !== expectedVersion) throw new Error('页面与后台版本不一致，请关闭旧程序并重新启动新版。');
    nativeRich = capabilities.native_rich;
    browserPairingKey = capabilities.browser_pairing_key || token;
    canCopyVideo = capabilities.copy_video;
    $('merge-component').textContent = capabilities.ffmpeg ? 'HLS / DASH 合并组件：已就绪' : 'HLS / DASH 合并需要免费的 FFmpeg；文件和普通视频直链不需要。';
    clipboardReady = true;
    $('copy-hint').textContent = nativeRich
      ? `${capabilities.platform==='darwin'?'macOS':'Windows'} 图文复制已启用：右侧一次复制图片、标题、摘要与真实链接。`
      : '图文复制已启用：右侧包含图片和可点击链接，适用于支持富文本的粘贴目标。';
    setBusy(busy);
    $('video-download').disabled = !(downloadCatalog?.resources?.length || current?.videos?.length);
    restoreVideoJob();
    refreshBrowserStatus();
    if(location.hash==='#browser-preview')$('capture-preview').click();
    else if(location.hash==='#browser-capture')$('capture-load').click();
  })
  .catch(error => { clipboardReady = false; $('copy-text').disabled = true; $('video-download').disabled = true; feedback(error.message, true); });

function displayVideoChoices(preview) {
  const choices = $('video-choice');
  choices.replaceChildren();
  const videos = preview.videos || [];
  if (!videos.length) {
    choices.add(new Option('当前链接未识别到完整视频', ''));
  } else {
    videos.forEach(video => {
      const duration = video.duration_ms ? ` · ${Math.round(video.duration_ms / 1000)} 秒` : '';
      choices.add(new Option(`视频 ${video.index}${duration} · ${video.qualities[0] || 'MP4'}`, video.index));
    });
    choices.value = String(preview.selected_video || videos[0].index);
  }
  choices.disabled = !videos.length;
  $('video-download').disabled = !videos.length || !clipboardReady;
  $('video-hint').textContent = videos.length
    ? `识别到 ${videos.length} 个视频。多连接下载，可暂停续传；优先选择 500 MB 内的最高可用画质。`
    : '也可以在下方输入文件、视频或网页地址，识别通用下载资源。';
}

function videoError(message) {
  $('video-feedback').textContent = message;
  $('video-feedback').hidden = !message;
}

function sizeText(bytes) {
  return (bytes / 1_000_000).toFixed(1) + ' MB';
}

function displayVideoJob(job) {
  videoJob = job;
  videoJobId = job.id;
  try { localStorage.setItem('linkexpand-video-job', job.id); } catch (_) {}
  $('video-task').hidden = false;
  const labels = {queued: '排队中', resolving: '读取视频信息', downloading: '正在下载',
    pausing: '暂停中', paused: '已暂停', merging: '正在合并音视频', verifying: '检查完整性', complete: '下载完成',
    cancelling: '取消中', cancelled: '已取消', error: '下载失败'};
  $('video-status').textContent = labels[job.status] || job.status;
  $('video-percent').textContent = job.progress == null ? '' : Math.round(job.progress * 100) + '%';
  if (job.progress == null) $('video-progress').removeAttribute('value');
  else $('video-progress').value = job.progress;
  $('video-detail').textContent = [job.quality, `${sizeText(job.downloaded)} / ${job.total ? sizeText(job.total) : '大小未知'}`,
    job.speed ? `${sizeText(job.speed)}/秒` : '',job.fragments ? `${job.fragments} 个分段已保存` : ''].filter(Boolean).join(' · ');
  $('video-pause').hidden = !['queued', 'resolving', 'downloading'].includes(job.status);
  $('video-resume').hidden = !['paused', 'error'].includes(job.status);
  $('video-resume').textContent = job.resumable || job.fragments ? '继续下载' : '重新下载';
  $('video-cancel').hidden = ['complete', 'cancelled'].includes(job.status);
  $('video-save').hidden = job.status !== 'complete';
  $('video-copy').disabled = job.status !== 'complete' || !canCopyVideo;
  $('video-path').hidden = !job.path;
  $('video-path').textContent = job.path ? '已保存到：' + job.path : '';
  videoError(job.error || (job.credentials_missing && job.status !== 'complete' ? '这个任务需要重新提供请求头。请再次识别地址或从浏览器扩展导入，然后点击下载以继续。' : ''));
  if (['queued', 'resolving', 'downloading', 'pausing', 'merging', 'verifying', 'cancelling'].includes(job.status)) pollVideoJob();
}

async function getVideoJob(id) {
  const response = await fetch('/api/video/jobs/' + encodeURIComponent(id), {headers: {'X-Local-Token': token}});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '无法读取下载任务。');
  return result;
}

async function restoreVideoJob() {
  try {
    const id = localStorage.getItem('linkexpand-video-job');
    if (id) displayVideoJob(await getVideoJob(id));
  } catch (_) { try { localStorage.removeItem('linkexpand-video-job'); } catch (_) {} }
}

async function pollVideoJob() {
  if (videoPolling) return;
  videoPolling = true;
  try {
    while (videoJobId) {
      await new Promise(resolve => setTimeout(resolve, 700));
      const requestedId = videoJobId;
      const job = await getVideoJob(requestedId);
      if (requestedId !== videoJobId) continue;
      displayVideoJob(job);
      if (['paused', 'complete', 'cancelled', 'error'].includes(job.status)) break;
    }
  } catch (error) { videoError(error.message); }
  finally { videoPolling = false; }
}

$('video-download').addEventListener('click', async () => {
  if ((!current && !downloadCatalog) || !clipboardReady || !$('video-choice').value) return;
  $('video-download').disabled = true;
  videoError('');
  try {
    const job=downloadCatalog
      ? await api('/api/download/start',{catalog_id:downloadCatalog.id,index:Number($('video-choice').value),
          connections:Number($('download-connections').value),speed_limit:Number($('download-speed').value)*1000})
      : await api('/api/video/start', {id: current.id, video_index: Number($('video-choice').value),
          connections:Number($('download-connections').value),speed_limit:Number($('download-speed').value)*1000});
    displayVideoJob(job);refreshQueue();
  }
  catch (error) { videoError(error.message); }
  finally { $('video-download').disabled = !(downloadCatalog?.resources?.length || current?.videos?.length) || !clipboardReady; }
});

for (const action of ['pause', 'resume', 'cancel']) {
  $('video-' + action).addEventListener('click', async () => {
    if (!videoJobId) return;
    const button = $('video-' + action);
    button.disabled = true;
    try { displayVideoJob(await api('/api/video/' + action, {job_id: videoJobId})); }
    catch (error) { videoError(error.message); }
    finally { button.disabled = false; }
  });
}
$('video-copy').addEventListener('click', async () => {
  if (!videoJobId || videoJob?.status !== 'complete') return;
  try {
    await api('/api/video/copy', {job_id: videoJobId});
    toast('完整文件已复制，可粘贴到聊天或资源管理器');
  } catch (error) { videoError(error.message); }
});
$('video-save').addEventListener('click', () => {
  if (!videoJobId || videoJob?.status !== 'complete') return;
  const link = document.createElement('a');
  link.href = '/downloads/' + videoJobId + '/file';
  link.download = videoJob.filename;
  link.click();
});

function displayCatalog(catalog) {
  downloadCatalog=catalog;
  $('video-choice').replaceChildren();
  for (const item of catalog.resources) $('video-choice').add(new Option(`${item.kind.toUpperCase()} · ${item.filename || item.qualities[0] || '资源 '+item.index}`,item.index));
  $('video-choice').disabled=!catalog.resources.length;
  $('video-download').disabled=!catalog.resources.length || !clipboardReady;
  $('video-hint').textContent=`${catalog.title} · 找到 ${catalog.resources.length} 个可下载资源`;
  videoError('');
}
async function resolveDownload(scan) {
  if (!clipboardReady)return;
  const url=$('download-url').value.trim() || $('url-input').value.trim();
  if (!url) {videoError('请输入下载地址。');return;}
  const buttons=[$('download-resolve'),$('download-scan')];buttons.forEach(button=>button.disabled=true);
  videoError('');$('video-hint').textContent=scan?'动态识别中，最多等待约 28 秒…':'正在识别文件和媒体资源…';
  try {
    const headers=$('download-headers').value.trim()?JSON.parse($('download-headers').value):{};
    const status=await browserStatus();
    if (status.connected && !Object.keys(headers).length) {
      // File links and public X APIs still use the downloader directly.
      let catalog;
      try { catalog=await api('/api/download/resolve',{url,headers,scan:false,bili_parser:$('bili-parser').checked}); } catch (_) {}
      if (catalog?.resources?.length) displayCatalog(catalog);
      else {
        const result=await browserExpand(url);
        if (!result.catalog?.resources?.length) throw new Error('浏览器已读取网页，但尚未发现可下载媒体。需要安全验证时，请在浏览器完成后重试；也可手动捕获播放请求。');
        displayCatalog(result.catalog);
      }
    } else displayCatalog(await api('/api/download/resolve',{url,headers,scan,bili_parser:$('bili-parser').checked}));
  } catch (error) {videoError(error.message);}
  finally {buttons.forEach(button=>button.disabled=false);}
}
$('download-resolve').addEventListener('click',()=>resolveDownload(false));
$('download-scan').addEventListener('click',()=>resolveDownload(true));
$('download-url').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();resolveDownload(false);}});
$('capture-pair').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(browserPairingKey || token);toast('配对码已复制。在扩展填写地址和配对码，点击“启用自动联动”；软件重启后仍然有效。');}catch(error){videoError(error.message);}});
async function refreshBrowserStatus() {
  try {
    const status=await browserStatus();
    $('browser-status').textContent=status.connected ? `${status.browsers.join(' / ')} 已连接 · 优先读取已正常打开的页面` : '可以直接展开链接 · 浏览器扩展未连接（选用）';
  } catch (_) { $('browser-status').textContent='浏览器联动暂时无法连接'; }
}
setInterval(refreshBrowserStatus, 5000);
async function importPreview(preview) {
  clearTimeout(expandTimer);requestController?.abort();const job=++revision;
  $('url-input').value=preview.url;lastRequested=preview.url;setBusy(true);
  await display(preview,job);
}
$('capture-preview').addEventListener('click',async()=>{
  try {
    const response=await fetch('/api/capture/preview',{headers:{'X-Local-Token':token}});
    const preview=await response.json();if(!response.ok)throw new Error(preview.error);
    await importPreview(preview);
  }catch(error){feedback(error.message,true);}
});
$('capture-load').addEventListener('click',async()=>{
  try {
    const response=await fetch('/api/download/catalogs',{headers:{'X-Local-Token':token}});
    const catalogs=await response.json();if(!response.ok)throw new Error(catalogs.error);
    if(!catalogs.length)throw new Error('尚未导入浏览器捕获资源。');
    const last=catalogs[catalogs.length-1];
    const catalog=await api('/api/download/catalog',{id:last.id});
    if(catalog.preview)await importPreview(catalog.preview);
    displayCatalog(catalog);
  }catch(error){videoError(error.message);}
});
window.addEventListener('hashchange',()=>{
  if(!clipboardReady)return;
  if(location.hash==='#browser-preview')$('capture-preview').click();
  else if(location.hash==='#browser-capture')$('capture-load').click();
});
async function refreshQueue() {
  try {
    const response=await fetch('/api/video/jobs',{headers:{'X-Local-Token':token}});
    const jobs=await response.json();if(!response.ok)throw new Error(jobs.error);
    $('download-queue').replaceChildren();
    for(const job of jobs.slice().reverse()){
      const row=document.createElement('button');row.className='queue-row';
      row.textContent=`${job.status} · ${job.filename || job.source} · ${sizeText(job.downloaded)}`;
      row.addEventListener('click',()=>displayVideoJob(job));$('download-queue').append(row);
    }
  }catch(error){videoError(error.message);}
}
$('queue-refresh').addEventListener('click',refreshQueue);
