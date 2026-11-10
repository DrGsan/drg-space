import test from 'node:test';
import assert from 'node:assert/strict';
import {mountRss} from '../web/modules/dashboard/widgets/rss.js';

const settle=()=>new Promise(resolve=>setImmediate(resolve));
const headlines=title=>({items:[{title,url:'https://example.com/news',source:'News',published:1}],errors:[]});

function environment(t,api) {
  const listeners=new Map();let now=1000,timer,cleared=false,deadline;
  const document={hidden:false,addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:(name,fn)=>{if(listeners.get(name)===fn)listeners.delete(name);}};
  const previous=Object.getOwnPropertyDescriptor(globalThis,'document');
  globalThis.document=document;
  t.after(()=>{if(previous)Object.defineProperty(globalThis,'document',previous);else delete globalThis.document;});
  t.mock.method(Date,'now',()=>now);
  t.mock.method(globalThis,'setTimeout',(fn,delay)=>{assert.equal(delay,600000);timer=fn;deadline=now+delay;cleared=false;return 1;});
  t.mock.method(globalThis,'clearTimeout',id=>{assert.ok(id===1 || id===undefined);cleared=true;});
  const host={isConnected:true,html:'',button:null,status:null,
    set innerHTML(value){this.html=value;this.button=value.includes('data-refresh-rss')?{disabled:false,textContent:'',onclick:null}:null;this.status=value.includes('data-rss-status')?{textContent:''}:null;},
    get innerHTML(){return this.html;},
    querySelector(selector){return selector==='[data-refresh-rss]'?this.button:selector==='[data-rss-status]'?this.status:null;},
  };
  const dispose=mountRss(host,{instance:{id:'news'},api});
  return {host,document,dispose,advance(ms){now+=ms;},pulse(){timer();},visibility(){listeners.get('visibilitychange')?.();},listeners,isCleared:()=>cleared,deadline:()=>deadline};
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
