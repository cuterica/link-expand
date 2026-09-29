const fs = require('fs');
const vm = require('vm');
const listeners = {};
const session = {};
const chrome = {
  storage: {session: {get: async key => ({[key]: session[key] ? JSON.parse(JSON.stringify(session[key])) : undefined}), set: async data => Object.assign(session,JSON.parse(JSON.stringify(data)))}},
  webRequest: Object.fromEntries(['onBeforeSendHeaders','onResponseStarted','onCompleted','onErrorOccurred'].map(name => [name,{addListener: callback => listeners[name]=callback}])),
  runtime: {onMessage:{addListener: callback => listeners.message=callback}}
};
vm.runInNewContext(fs.readFileSync('browser-extension/worker.js','utf8'),{chrome,Map,Date,console,importScripts(){}});
const tick = () => new Promise(resolve=>setImmediate(resolve));
(async()=>{
  await new Promise(resolve=>listeners.message({action:'start',tabId:9,source:'https://example.com/page'},null,resolve));
  listeners.onBeforeSendHeaders({method:'GET',tabId:9,requestId:'one',requestHeaders:[{name:'Cookie',value:'test-secret'},{name:'Referer',value:'https://example.com/page'}]});
  listeners.onResponseStarted({method:'GET',tabId:9,requestId:'one',url:'https://cdn.example.com/video.mp4',responseHeaders:[{name:'Content-Type',value:'video/mp4'}]});
  listeners.onCompleted({requestId:'one'});
  await tick();await tick();
  if(session.captureState[9].resources.length!==1 || session.captureState[9].resources[0].headers.Cookie!=='test-secret')throw Error('Captured context was lost.');
  listeners.onResponseStarted({method:'GET',tabId:77,requestId:'other',url:'https://unwatched.example.com/a.mp4',responseHeaders:[{name:'Content-Type',value:'video/mp4'}]});
  await tick();
  if(session.captureState[77])throw Error('Unwatched tab was captured.');
  for(let index=0;index<10;index++)listeners.onResponseStarted({method:'GET',tabId:9,requestId:'parallel-'+index,url:`https://cdn.example.com/part-${index}.mp4`,responseHeaders:[{name:'Content-Type',value:'video/mp4'}]});
  await new Promise(resolve=>listeners.message({action:'list',tabId:9},null,resolve));
  if(session.captureState[9].resources.length!==11)throw Error('Parallel response capture lost resources.');
  await new Promise(resolve=>listeners.message({action:'stop',tabId:9},null,resolve));
  if(session.captureState[9])throw Error('Stop did not clear captured data.');
  console.log('PASS: explicit tab, preserved request context, concurrent resources, unwatched tab ignored, stop clears data');
})().catch(error=>{console.error(error);process.exit(1)});
