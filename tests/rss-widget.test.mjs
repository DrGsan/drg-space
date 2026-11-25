import test from 'node:test';
import assert from 'node:assert/strict';
import {mountRss} from '../web/modules/dashboard/widgets/rss.js';

const settle=()=>new Promise(resolve=>setImmediate(resolve));
const headlines=title=>({items:[{title,url:'https://example.com/news',source:'News',published:1}],errors:[]});

function environment(t,api) {
  const listeners=new Set(),timers=new Map(),state={me:{id:'first'}};
  let now=1000,timerID=0,deadline;
  const document={hidden:false,addEventListener:(_name,fn)=>listeners.add(fn),removeEventListener:(_name,fn)=>listeners.delete(fn)};
  const previous=Object.getOwnPropertyDescriptor(globalThis,'document');
  globalThis.document=document;
  t.after(()=>{if(previous)Object.defineProperty(globalThis,'document',previous);else delete globalThis.document;});
  t.mock.method(Date,'now',()=>now);
  t.mock.method(globalThis,'setTimeout',(fn,delay)=>{deadline=now+delay;timers.set(++timerID,{fn,at:deadline});return timerID;});
  t.mock.method(globalThis,'clearTimeout',id=>timers.delete(id));
  function mount(config){
    const host={isConnected:true,html:'',button:null,status:null,
      set innerHTML(value){this.html=value;this.button=value.includes('data-refresh-rss')?{disabled:false,textContent:'',onclick:null}:null;this.status=value.includes('data-rss-status')?{textContent:''}:null;},
      get innerHTML(){return this.html;},
      querySelector(selector){return selector==='[data-refresh-rss]'?this.button:selector==='[data-rss-status]'?this.status:null;},
    };
    const dispose=mountRss(host,{state,instance:{id:'news',config},api});
    return {host,dispose};
  }
  return {...mount(),mount,state,document,advance(ms){now+=ms;},
    pulse(){for(const [id,{fn,at}] of [...timers])if(at<=now){timers.delete(id);fn();}},
    visibility(){for(const notify of listeners)notify();},listeners,isCleared:()=>!timers.size,deadline:()=>deadline};
}

test('manual refresh bypasses cache and keeps headlines while a single request runs',async t=>{
  const calls=[];let resolve;
  const env=environment(t,path=>{calls.push(path);return calls.length===1?Promise.resolve(headlines('First')):new Promise(done=>{resolve=done;});});
  await settle();assert.match(env.host.innerHTML,/First/);
  assert.equal(env.deadline(),601000);env.advance(240000);
  const first=env.host.button.onclick(),second=env.host.button.onclick();
  assert.equal(first,second);assert.equal(env.host.button.disabled,true);
  assert.equal(env.deadline(),841000);
  assert.match(env.host.innerHTML,/First/);
  await settle();assert.deepEqual(calls,['/widgets/news/rss','/widgets/news/rss?refresh=true']);
  resolve(headlines('Second'));await first;
  assert.match(env.host.innerHTML,/Second/);assert.equal(env.host.button.disabled,false);
  env.dispose();
});

test('stale feeds refresh when returning to the tab and timers stop on disposal',async t=>{
  const calls=[];const env=environment(t,async path=>{calls.push(path);return headlines('Current');});
  await settle();env.document.hidden=true;env.advance(600000);env.pulse();await settle();
  assert.equal(calls.length,1);
  env.document.hidden=false;env.visibility();await settle();assert.equal(calls.length,2);
  assert.equal(calls[1],'/widgets/news/rss?refresh=true');
  env.visibility();await settle();assert.equal(calls.length,2);
  env.advance(600000);env.pulse();await settle();assert.equal(calls.length,3);
  assert.equal(calls[2],'/widgets/news/rss?refresh=true');
  env.dispose();assert.equal(env.listeners.size,0);assert.equal(env.isCleared(),true);
  env.advance(600000);env.pulse();await settle();assert.equal(calls.length,3);
});

test('failed background refresh preserves visible headlines and re-enables retry',async t=>{
  let calls=0;const env=environment(t,async()=>{if(++calls===1)return headlines('Keep me');throw new Error('Network unavailable');});
  await settle();await env.host.button.onclick();
  assert.match(env.host.innerHTML,/Keep me/);assert.equal(env.host.status.textContent,'Network unavailable');
  assert.equal(env.host.button.disabled,false);env.dispose();
});

test('unmounting during the first request prevents stale rendering',async t=>{
  let resolve;const env=environment(t,()=>new Promise(done=>{resolve=done;}));
  await settle();env.dispose();resolve(headlines('Discard me'));await settle();
  assert.doesNotMatch(env.host.innerHTML,/Discard me/);assert.equal(env.listeners.size,0);
});

test('remounts share an in-flight request and a fresh result until its original deadline',async t=>{
  const calls=[];let resolve;
  const env=environment(t,path=>{calls.push(path);return new Promise(done=>{resolve=done;});});
  env.dispose();
  const next=env.mount(),overlap=env.mount();
  assert.deepEqual(calls,['/widgets/news/rss']);
  resolve(headlines('First'));await settle();
  assert.match(next.host.innerHTML,/First/);assert.match(overlap.host.innerHTML,/First/);
  assert.doesNotMatch(env.host.innerHTML,/First/);
  next.dispose();overlap.dispose();
  env.advance(590000);
  const fresh=env.mount();
  assert.match(fresh.host.innerHTML,/First/);assert.equal(calls.length,1);
  assert.equal(env.deadline(),601000);
  env.advance(10000);env.pulse();
  assert.deepEqual(calls,['/widgets/news/rss','/widgets/news/rss?refresh=true']);
  assert.match(fresh.host.innerHTML,/First/);
  resolve(headlines('Fresh after expiry'));await settle();
  assert.match(fresh.host.innerHTML,/Fresh after expiry/);fresh.dispose();
});

test('changed feed, label, item limit and session exclude earlier results including late responses',async t=>{
  const replies=[];
  const env=environment(t,()=>new Promise(resolve=>replies.push(resolve)));
  const first={url:'https://one.example/feed',label:'One'},second={url:'https://two.example/feed',label:'Two'};
  const config1=env.mount({feeds:[first]});
  const config2=env.mount({feeds:[second]});
  const renamed=env.mount({feeds:[{...second,label:'Other'}]});
  const smaller=env.mount({feeds:[{...second,label:'Other'}],limit:3});
  assert.equal(replies.length,5);
  replies[4](headlines('Small feed'));await settle();
  for(const reply of replies.slice(0,4))reply(headlines('Obsolete feed'));
  await settle();
  assert.match(smaller.host.innerHTML,/Small feed/);
  assert.doesNotMatch(smaller.host.innerHTML,/Obsolete/);
  for(const view of [env,config1,config2,renamed,smaller])view.dispose();

  const config={feeds:[second]};
  env.state.me={id:'second'};
  const otherUser=env.mount(config);
  assert.equal(replies.length,6);assert.doesNotMatch(otherUser.host.innerHTML,/Small feed/);
  env.state.me={id:'second'};
  const newSession=env.mount(config);
  assert.equal(replies.length,7);
  replies[6](headlines('New session'));replies[5](headlines('Old session'));await settle();
  assert.match(newSession.host.innerHTML,/New session/);
  assert.doesNotMatch(otherUser.host.innerHTML,/Old session/);
  otherUser.dispose();newSession.dispose();
});

test('rate limited load stays visible on remount and retries once at Retry-After',async t=>{
  let calls=0;
  const env=environment(t,async()=>{
    if(++calls===1)throw Object.assign(new Error('Rate limited'),{status:429,retryAfter:'12'});
    return headlines('Recovered');
  });
  await settle();assert.equal(env.host.status.textContent,'Rate limited');env.dispose();
  const next=env.mount();assert.equal(next.host.status.textContent,'Rate limited');assert.equal(calls,1);
  env.advance(11999);env.pulse();assert.equal(calls,1);
  env.advance(1);env.pulse();await settle();assert.equal(calls,2);
  assert.match(next.host.innerHTML,/Recovered/);next.dispose();
});

test('partial feed failure keeps the successful headlines and retries through the per-source cache',async t=>{
  const calls=[];
  const env=environment(t,async path=>{
    calls.push(path);
    if(calls.length===1)return {...headlines('Working source'),errors:[{source:'Other feed',message:'Offline'}]};
    return headlines('Other feed is back');
  });
  await settle();assert.match(env.host.innerHTML,/Working source/);assert.match(env.host.innerHTML,/Offline/);
  env.dispose();env.advance(59000);
  const next=env.mount();assert.match(next.host.innerHTML,/Working source/);assert.equal(calls.length,1);
  env.advance(1000);env.pulse();await settle();
  assert.deepEqual(calls,['/widgets/news/rss','/widgets/news/rss']);
  assert.match(next.host.innerHTML,/Other feed is back/);next.dispose();
});

test('denied access drops previously authorized headlines for the mounted widget and its cache',async t=>{
  let calls=0;
  const env=environment(t,async()=>{
    if(++calls===1)return headlines('Previously authorized');
    throw Object.assign(new Error('Forbidden'),{status:403});
  });
  await settle();assert.match(env.host.innerHTML,/Previously authorized/);
  await env.host.button.onclick();
  assert.equal(env.host.status.textContent,'Forbidden');assert.doesNotMatch(env.host.innerHTML,/Previously authorized/);
  env.dispose();
  const next=env.mount();assert.equal(next.host.status.textContent,'Forbidden');
  assert.doesNotMatch(next.host.innerHTML,/Previously authorized/);next.dispose();
});
