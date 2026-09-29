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
  const config = await chrome.storage.local.get(['base', 'token', 'autoBridge', 'bridgeLastError', 'screenshots']);
  if (config.base) $('base').value = config.base;
  if (config.token) $('token').value = config.token;
  if (config.screenshots !== undefined) $('auto-screenshot').checked = config.screenshots;
  $('auto-status').textContent = config.autoBridge ? '自动联动已启用' + (config.bridgeLastError ? ' · ' + config.bridgeLastError : '') : '自动联动尚未启用';
  await refresh();
})();
$('auto-enable').onclick = async () => {
  try {
    const screenshots = $('auto-screenshot').checked;
    const granted = await chrome.permissions.request({origins: ['http://*/*', 'https://*/*'], permissions: screenshots ? ['cookies', 'debugger'] : ['cookies']});
    if (!granted) throw new Error('需要网站读取权限，才能自动读取你在软件提交的网页和媒体。');
    const result = await chrome.runtime.sendMessage({action: 'bridge-config', enabled: true, base: $('base').value, token: $('token').value, screenshots});
    if (result?.error) throw new Error(result.error);
    $('auto-status').textContent = '自动联动已启用；现在只需在软件输入链接。';
  } catch (error) { $('auto-status').textContent = error.message; }
};
$('auto-disable').onclick = async () => {
  await chrome.runtime.sendMessage({action: 'bridge-config', enabled: false});
  $('auto-status').textContent = '自动联动已关闭';
};
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
async function readPreview() {
  const [result]=await chrome.scripting.executeScript({target:{tabId:active.id},func:()=>{
    const meta=name=>document.querySelector(`meta[property="${name}"],meta[name="${name}"]`)?.content || '';
    const text=value=>(value || '').replace(/\s+/g,' ').trim();
    const title=text(meta('og:title') || meta('twitter:title') || document.title).slice(0,180);
    if (/出错啦.*bilibili/i.test(title))throw new Error('页面仍被 B 站拦截，请先在浏览器中正常打开并播放。');
    const description=text(meta('og:description') || meta('twitter:description') || meta('description') ||
      [...document.querySelectorAll('article p,main p')].slice(0,5).map(node=>node.textContent).join(' ')).slice(0,500);
    let image=meta('og:image:secure_url') || meta('og:image') || meta('twitter:image') || document.querySelector('video[poster]')?.poster || '';
    if (!image) {
      const images=[...document.images].filter(node=>node.naturalWidth>=280 && node.naturalHeight>=140 && !/avatar|logo|icon/i.test(node.currentSrc || node.src));
      images.sort((a,b)=>(b.naturalWidth*b.naturalHeight)-(a.naturalWidth*a.naturalHeight));
      image=images[0]?.currentSrc || images[0]?.src || '';
    }
    if(image)try {image=new URL(image,location.href).href;}catch(_){image='';}
    if (!/^https?:/.test(image))image='';
    return {url:location.href,title,description,image_url:image,site_name:text(meta('og:site_name'))};
  }});
  if (!result?.result?.title)throw new Error('无法读取此页标题，请确认当前标签页是普通网页。');
  return result.result;
}
async function importData(candidates,preview) {
  const base=new URL($('base').value);
  if (base.protocol!=='http:' || !['127.0.0.1','localhost'].includes(base.hostname))throw new Error('只能连接本机的 Link Expand。');
  const token=$('token').value.trim();
  const response=await fetch(base.origin+'/api/capture/import',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},
    body:JSON.stringify({source:preview.url,candidates,preview})});
  const result=await response.json();
  if(!response.ok)throw new Error(result.error || '导入失败，请检查配对码，并使用 v0.3.2 或更新版本的软件。');
  if(!result.preview)throw new Error('后台版本过旧，网页预览没有导入；请启动 v0.3.2 或更新的软件。');
  await chrome.storage.local.set({base:base.origin,token});
  await chrome.tabs.create({url:base.origin+(candidates.length?'/#browser-capture':'/#browser-preview')});
  return result;
}
$('preview').onclick=async()=>{
  try{await importData([],await readPreview());$('status').textContent='当前网页已导入，在软件点击“读取浏览器预览”。';}
  catch(error){$('status').textContent=error.message;}
};
$('import').onclick = async () => {
  try {
    const data = await chrome.runtime.sendMessage({action: 'list', tabId: active.id});
    const candidates = [...$('resources').selectedOptions].map(option => data.resources[Number(option.value)]);
    if (!candidates.length) throw new Error('先选择要导入的资源。');
    const result=await importData(candidates,await readPreview());
    $('status').textContent = `已导入预览和 ${result.resources.length} 个资源，在软件点击“读取浏览器捕获”。`;
  } catch (error) { $('status').textContent = error.message; }
};
