const fs=require('fs'),vm=require('vm');
const elements=new Map();
const element=id=>{if(!elements.has(id))elements.set(id,{value:id==='base'?'http://127.0.0.1:8765':id==='token'?'paired-token':'',replaceChildren(){},add(){},selectedOptions:[]});return elements.get(id)};
let posted,opened;
const meta={'og:title':'已登录 Edge 的视频标题','og:description':'这个摘要来自已经打开的页面','og:image':'https://i0.hdslb.com/bfs/archive/cover.jpg'};
const document={getElementById:element,title:'Fallback',images:[],querySelector(selector){for(const name of Object.keys(meta))if(selector.includes('"'+name+'"'))return {content:meta[name]};return null;},querySelectorAll(){return []}};
const chrome={tabs:{query:async()=>[{id:9,url:'https://www.bilibili.com/video/BV1cSec6tEux/'}],create:async data=>{opened=data.url}},
 storage:{local:{get:async()=>({}),set:async()=>{}}},runtime:{sendMessage:async()=>null},
 permissions:{request:async()=>true},scripting:{executeScript:async({target,func})=>{if(target.tabId!==9)throw Error('Wrong tab');return [{result:func()}]}}};
const sandbox={chrome,document,URL,Option:function(){},location:{href:'https://www.bilibili.com/video/BV1cSec6tEux/'},
 fetch:async(url,options)=>{posted=JSON.parse(options.body);return {ok:true,json:async()=>({preview:{id:'preview-id'},resources:[]})}}};
vm.runInNewContext(fs.readFileSync('browser-extension/popup.js','utf8'),sandbox);
(async()=>{
 await new Promise(resolve=>setImmediate(resolve));
 await element('preview').onclick();
 if(posted.preview.title!==meta['og:title'] || posted.preview.image_url!==meta['og:image'])throw Error('Page metadata missing.');
 if(posted.candidates.length || posted.preview.Cookie || posted.preview.Authorization)throw Error('Preview imported unrelated credentials.');
 if(opened!=='http://127.0.0.1:8765/#browser-preview')throw Error('Software preview was not opened.');
 if(!element('status').textContent.includes('已导入'))throw Error(element('status').textContent);
 console.log('PASS: metadata from selected signed-in tab, no credentials in preview, paired import opens software.');
})().catch(error=>{console.error(error);process.exit(1)});
