let active;
const $ = id => document.getElementById(id);
async function refresh() {
  const data = await chrome.runtime.sendMessage({action: 'list', tabId: active.id});
  $('resources').replaceChildren();
  for (const [index, resource] of (data?.resources || []).entries()) $('resources').add(new Option(`${resource.kind.toUpperCase()} · ${resource.url}`, index));
  $('status').textContent = data ? `已识别 ${data.resources.length} 个资源` : '尚未启用捕获';
}
(async () => {
  [active] = await chrome.tabs.query({active: true, currentWindow: true});
  const config = await chrome.storage.local.get(['base', 'token']);
  if (config.base) $('base').value = config.base;
  if (config.token) $('token').value = config.token;
  await refresh();
})();
$('start').onclick = async () => {
  try {
    const granted = await chrome.permissions.request({origins: ['http://*/*', 'https://*/*']});
    if (!granted) throw new Error('需要网站请求访问权限才能识别跨域视频服务器。');
    await chrome.runtime.sendMessage({action: 'start', tabId: active.id, source: active.url});
    $('status').textContent = '已启用，请在当前页面播放视频，然后刷新列表。';
  } catch (error) { $('status').textContent = error.message; }
};
$('stop').onclick = async () => { await chrome.runtime.sendMessage({action: 'stop', tabId: active.id}); await refresh(); };
$('refresh').onclick = refresh;
$('import').onclick = async () => {
  try {
    const base = new URL($('base').value);
    if (base.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(base.hostname)) throw new Error('只能连接本机的 Link Expand。');
    const data = await chrome.runtime.sendMessage({action: 'list', tabId: active.id});
    const candidates = [...$('resources').selectedOptions].map(option => data.resources[Number(option.value)]);
    if (!candidates.length) throw new Error('先选择要导入的资源。');
    const token = $('token').value.trim();
    const response = await fetch(base.origin + '/api/capture/import', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Local-Token': token}, body: JSON.stringify({source: data.source, candidates})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '导入失败，请检查配对码。');
    await chrome.storage.local.set({base: base.origin, token});
    $('status').textContent = `已导入 ${result.resources.length} 个资源，在软件点击“读取浏览器捕获”即可查看。`;
  } catch (error) { $('status').textContent = error.message; }
};
