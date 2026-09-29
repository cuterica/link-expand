const pending = new Map();
const expires = 10 * 60 * 1000;
let storageQueue = Promise.resolve();
function serialized(action) {
  const result = storageQueue.then(action);
  storageQueue = result.catch(() => {});
  return result;
}
async function state() { return (await chrome.storage.session.get('captureState')).captureState || {}; }
async function watched(tabId) {
  const tabs = await state();
  return tabs[tabId] && Date.now() - tabs[tabId].started < expires;
}
chrome.webRequest.onBeforeSendHeaders.addListener(details => {
  if (details.method !== 'GET' || details.tabId < 0) return;
  const ready = watched(details.tabId).then(enabled => {
    if (!enabled) return;
    const headers = {};
    for (const header of details.requestHeaders || []) {
      if (['referer', 'origin', 'user-agent', 'cookie', 'authorization'].includes(header.name.toLowerCase())) headers[header.name] = header.value || '';
    }
    return headers;
  });
  pending.set(details.requestId, ready);
  if (pending.size > 100) pending.delete(pending.keys().next().value);
}, {urls: ['http://*/*', 'https://*/*']}, ['requestHeaders', 'extraHeaders']);
chrome.webRequest.onResponseStarted.addListener(details => {
  if (details.method !== 'GET' || details.tabId < 0) return;
  const capturedHeaders = pending.get(details.requestId);
  serialized(async () => {
    const tabs = await state();
    const tab = tabs[details.tabId];
    if (!tab || Date.now() - tab.started >= expires) return;
    const response = Object.fromEntries((details.responseHeaders || []).map(h => [h.name.toLowerCase(), h.value || '']));
    const mime = (response['content-type'] || '').split(';')[0].toLowerCase();
    let kind = /\.m3u8(?:\?|$)/i.test(details.url) || /mpegurl/.test(mime) ? 'hls'
      : /\.mpd(?:\?|$)/i.test(details.url) || mime === 'application/dash+xml' ? 'dash'
      : mime.startsWith('video/') ? 'video' : mime.startsWith('audio/') ? 'audio'
      : /attachment/i.test(response['content-disposition'] || '') ? 'file' : null;
    if (!kind || details.url.includes('127.0.0.1') || details.url.includes('localhost')) return;
    tab.resources ||= [];
    if (!tab.resources.some(r => r.url === details.url)) {
      tab.resources.push({url: details.url, kind, mime, headers: (capturedHeaders ? await capturedHeaders : null) || {Referer: tab.source}});
      tab.resources = tab.resources.slice(-64);
      await chrome.storage.session.set({captureState: tabs});
    }
    pending.delete(details.requestId);
  });
}, {urls: ['http://*/*', 'https://*/*']}, ['responseHeaders', 'extraHeaders']);
chrome.webRequest.onCompleted.addListener(details => pending.delete(details.requestId), {urls: ['http://*/*', 'https://*/*']});
chrome.webRequest.onErrorOccurred.addListener(details => pending.delete(details.requestId), {urls: ['http://*/*', 'https://*/*']});
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (!['start', 'stop', 'list'].includes(message.action)) return;
  serialized(async () => {
    const tabs = await state();
    if (message.action === 'start') tabs[message.tabId] = {started: Date.now(), source: message.source, resources: []};
    if (message.action === 'stop') delete tabs[message.tabId];
    if (message.action === 'start' || message.action === 'stop') await chrome.storage.session.set({captureState: tabs});
    respond(tabs[message.tabId] || null);
  }).catch(error => respond({error: error.message}));
  return true;
});
importScripts('page.js', 'bridge.js');
