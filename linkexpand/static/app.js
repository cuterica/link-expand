'use strict';
const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="local-token"]').content;
let current = null;
let busy = false;
let cardBlob = null;
let toastTimer;
let expandTimer;
let lastRequested = '';
let revision = 0;
let requestController = null;
let nativeRich = false;
const actionIds = ['copy-image', 'copy-text', 'download', 'download-cover'];

async function api(path, data, signal) {
  const response = await fetch(path, {method: 'POST', signal,
    headers: {'X-Local-Token': token, 'Content-Type': 'application/json'}, body: JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '操作失败，请重试。');
  return result;
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
  if (!value) $('download-cover').disabled = !current?.cover;
}

async function display(preview, job) {
  if (job !== revision) return;
  current = preview;
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
  try { await display(await api('/api/preview', {url}, requestController.signal), job); }
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
  if (!current || busy) return;
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
  .then(response => { if (!response.ok) throw new Error('会话已更新，请刷新页面。'); return response.json(); })
  .then(capabilities => { nativeRich = capabilities.native_rich; })
  .catch(error => feedback(error.message, true));
