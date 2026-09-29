let bridgeBusy = false;
let bridgeRunning = null;
let bridgeTimer;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

function localBase(value) {
  const base = new URL(value);
  if (base.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(base.hostname)) throw new Error('只能连接本机的 Link Expand。');
  return base.origin;
}
async function bridgePost(config, path, data) {
  const response = await fetch(localBase(config.base) + path, {method: 'POST', signal: AbortSignal.timeout(12000),
    headers: {'Content-Type': 'application/json', 'X-Local-Token': config.token}, body: JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '连接失败');
  return result;
}
async function readTab(tabId, play) {
  const [result] = await chrome.scripting.executeScript({target: {tabId}, world: 'MAIN', func: linkExpandPage, args: [play]});
  return result?.result;
}
function selectResources(captured, discovered) {
  const resources = new Map();
  const declared = new Set(discovered.map(item=>item.url));
  // Observed request headers take priority over DOM-only addresses.
  for (const resource of [...discovered, ...captured]) {
    if (!/^https?:/.test(resource.url || '') || /\.ts(?:[?#]|$)/i.test(resource.url)) continue;
    if (/\.m4s(?:[?#]|$)/i.test(resource.url) && !declared.has(resource.url) && !/(^|\.)bilivideo\.com$/i.test(new URL(resource.url).hostname)) continue;
    const canonical = new URL(resource.url);
    if (/^\d+-\d+$/.test(canonical.searchParams.get('range') || '')) canonical.searchParams.delete('range');
    resources.set(canonical.href, {...resource, url: canonical.href});
  }
  const items = [...resources.values()];
  // Prefer the best DOM-declared representation; keep other captured candidates available.
  const preferred = discovered.filter(item => ['video', 'audio'].includes(item.kind));
  const videos = items.filter(item => item.kind === 'video'), audios = items.filter(item => item.kind === 'audio');
  if (videos.length && audios.length) {
    const best = kind => {
      const declared = preferred.find(item => item.kind === kind);
      return items.find(item => item.kind === kind && declared && item.url.split('?')[0] === declared.url.split('?')[0]) || items.find(item => item.kind === kind);
    };
    return [best('video'), best('audio'), ...items.filter(item => ['hls', 'dash', 'file'].includes(item.kind))].slice(0, 64);
  }
  return items.slice(0, 64);
}
async function inlineCover(preview) {
  if (!preview.image_url || preview.image_data) return;
  try {
    const response = await fetch(preview.image_url, {credentials: 'include', signal: AbortSignal.timeout(6000)});
    if (!response.ok || Number(response.headers.get('content-length')) > 2_000_000) return;
    const reader = response.body.getReader(), chunks = []; let size = 0;
    try {
      while (true) {
        const {value, done} = await reader.read(); if (done) break;
        size += value.length; if (size > 2_000_000) return; chunks.push(value);
      }
    } finally { await reader.cancel(); }
    const mime = response.headers.get('content-type')?.split(';')[0];
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(mime)) return;
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    let binary = ''; for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
    preview.image_data = `data:${mime};base64,${btoa(binary)}`;
  } catch (_) { /* Server can still try the public cover URL. */ }
}
async function screenshotOwnedTab(tabId, preview) {
  const target = {tabId}; let attached = false;
  try {
    await chrome.debugger.attach(target, '1.3'); attached = true;
    const layout = await chrome.debugger.sendCommand(target, 'Page.getLayoutMetrics');
    const viewport = layout.cssVisualViewport || layout.visualViewport;
    const image = await chrome.debugger.sendCommand(target, 'Page.captureScreenshot', {format: 'jpeg', quality: 75,
      captureBeyondViewport: false, clip: {x: viewport.pageX || 0, y: viewport.pageY || 0,
        width: Math.min(1280, viewport.clientWidth), height: Math.min(800, viewport.clientHeight), scale: 1}});
    if (image.data && image.data.length <= 2_666_668) {
      preview.image_data = 'data:image/jpeg;base64,' + image.data; preview.visual_source = '浏览器网页截图';
    }
  } catch (_) {} finally { if (attached) await chrome.debugger.detach(target).catch(() => {}); }
}
async function runBrowserJob(config, job) {
  let tabId;
  let owned = false;
  let session;
  bridgeRunning = {id: job.id, cancelled: false};
  try {
    // Read an already-working page first. Do not reload, mute, play or close it.
    const key = value => {
      try {
        const url=new URL(value),video=url.pathname.match(/\/video\/(BV[\w]+)/i);
        if (/(^|\.)bilibili\.com$/.test(url.hostname) && video) return 'bilibili:'+video[1]+':'+(url.searchParams.get('p') || '1');
        url.hash='';return url.href;
      }catch(_){return '';}
    };
    const existing=(await chrome.tabs.query({})).filter(tab=>tab.url && key(tab.url)===key(job.url));
    existing.sort((a,b)=>Number(b.active)-Number(a.active));
    for (const tab of existing) {
      try {
        const data=await readTab(tab.id,false);
        if (!data?.preview?.title || data.error) continue;
        await inlineCover(data.preview);
        const candidates=selectResources((await state())[tab.id]?.resources || [],data.candidates || []);
        if (bridgeRunning.cancelled) throw new Error('任务已取消。');
        await bridgePost(config,'/api/browser/result',{id:job.id,client_id:config.client_id,source:data.preview.url,preview:data.preview,candidates});
        await chrome.storage.local.set({bridgeLastError:''});return;
      } catch(error) { if (bridgeRunning.cancelled) throw error; }
    }
    // Otherwise create a tab using the same profile; only this tab is owned.
    const tab = await chrome.tabs.create({url: 'about:blank', active: false}); tabId = tab.id;
    owned = true;
    await chrome.storage.session.set({bridgeOwnedTab: tabId});
    await serialized(async () => {
      const tabs = await state(); tabs[tabId] = {started: Date.now(), source: job.url, resources: []};
      await chrome.storage.session.set({captureState: tabs});
    });
    await chrome.tabs.update(tabId, {url: job.url, muted: true});
    const started = Date.now(); let data;
    while (Date.now() - started < 28000) {
      if (bridgeRunning.cancelled) throw new Error('任务已取消。');
      await delay(1000);
      const tab = await chrome.tabs.get(tabId);
      if (tab.status !== 'complete') continue;
      data = await readTab(tabId, true);
      session=(await state())[tabId]?.pageHeaders;
      if (data?.error) throw new Error(data.error);
      const captured = (await state())[tabId]?.resources || [];
      if (data?.preview?.title && (captured.length || data.candidates.length || Date.now() - started > 8000)) {
        // Let the player start its audio request after the first video response.
        await delay(captured.length || data.candidates.length ? 2500 : 500);
        data = await readTab(tabId, false);
        break;
      }
    }
    if (!data?.preview?.title) throw new Error('网页未能正常载入，请在浏览器中打开并完成登录或验证后重试。');
    if (data.error) throw new Error(data.error);
    await inlineCover(data.preview);
    if (!data.preview.image_data && config.screenshots && !bridgeRunning.cancelled) await screenshotOwnedTab(tabId, data.preview);
    const captured = (await state())[tabId]?.resources || [];
    const candidates = selectResources(captured, data.candidates || []);
    if (bridgeRunning.cancelled) throw new Error('自动联动已关闭或任务已取消。');
    await bridgePost(config, '/api/browser/result', {id: job.id, client_id: config.client_id,
      source: data.preview.url, preview: data.preview, candidates});
    await chrome.storage.local.set({bridgeLastError: ''});
  } catch (error) {
    try {
      if (await chrome.permissions.contains({permissions:['cookies']})) session={cookies:await chrome.cookies.getAll({url:job.url}),headers:session || {}};
    } catch(_) {}
    if (!bridgeRunning?.cancelled) await bridgePost(config, '/api/browser/result', {id: job.id, client_id: config.client_id, error: error.message, session}).catch(() => {});
    if (!bridgeRunning?.cancelled) {
      await chrome.storage.local.set({bridgeLastError: error.message});
    }
  } finally {
    if (owned && tabId !== undefined) {
      await serialized(async () => { const tabs = await state(); delete tabs[tabId]; await chrome.storage.session.set({captureState: tabs}); });
      await chrome.tabs.remove(tabId).catch(() => {});
      await chrome.storage.session.remove('bridgeOwnedTab');
    }
    bridgeRunning = null;
  }
}
async function bridgeTick() {
  if (bridgeBusy) return;
  bridgeBusy = true;
  let interval = 30000;
  try {
    const config = await chrome.storage.local.get(['base', 'token', 'client_id', 'autoBridge', 'screenshots']);
    if (!config.autoBridge || !config.base || !config.token || !config.client_id) return;
    interval = 1000;
    const result = await bridgePost(config, '/api/browser/poll', {client_id: config.client_id,
      browser: /Edg\//.test(navigator.userAgent) ? 'Edge' : 'Chrome', active: bridgeRunning?.id});
    if (bridgeRunning && result.cancel) bridgeRunning.cancelled = true;
    if (!bridgeRunning && result.job) void runBrowserJob(config, result.job);
  } catch (_) { /* Software can be closed; alarms reconnect when it restarts. */ }
  finally { bridgeBusy = false; clearTimeout(bridgeTimer); bridgeTimer = setTimeout(bridgeTick, interval); }
}
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === 'link-expand-bridge') void bridgeTick(); });
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message.action !== 'bridge-config') return;
  (async () => {
    if (message.enabled) {
      const base = localBase(message.base), token = String(message.token || '').trim();
      if (!token) throw new Error('请填写软件的配对码。');
      const current = await chrome.storage.local.get('client_id');
      const config = {base, token, client_id: current.client_id || crypto.randomUUID(), autoBridge: true, screenshots: !!message.screenshots};
      await bridgePost(config, '/api/browser/poll', {client_id: config.client_id, browser: /Edg\//.test(navigator.userAgent) ? 'Edge' : 'Chrome'});
      await chrome.storage.local.set(config); void bridgeTick();
    } else {
      await chrome.storage.local.set({autoBridge: false});
      if (bridgeRunning) bridgeRunning.cancelled = true;
    }
    respond({ok: true});
  })().catch(error => respond({error: error.message}));
  return true;
});
(async () => {
  const {bridgeOwnedTab} = await chrome.storage.session.get('bridgeOwnedTab');
  if (bridgeOwnedTab !== undefined) {
    await chrome.tabs.remove(bridgeOwnedTab).catch(() => {});
    await serialized(async () => { const tabs = await state(); delete tabs[bridgeOwnedTab]; await chrome.storage.session.set({captureState: tabs}); });
    await chrome.storage.session.remove('bridgeOwnedTab');
  }
  await chrome.alarms.create('link-expand-bridge', {periodInMinutes: 0.5});
  void bridgeTick();
})();
