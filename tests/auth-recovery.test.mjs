import test from 'node:test';
import assert from 'node:assert/strict';
import {refreshSession} from '../web/core/auth.js';

function environment(fetch,locks){
  const original=new Map(['fetch','document','navigator'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
  for(const [key,value] of Object.entries({fetch,document:{cookie:'drg_csrf=fixture-token'},navigator:{locks}}))Object.defineProperty(globalThis,key,{value,configurable:true,writable:true});
  return ()=>{for(const [key,descriptor] of original)if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];};
}
test('concurrent callers share one refresh and propagate its bounded signal',async()=>{
  const calls=[];const restore=environment(async(path,options)=>{calls.push(path);assert.ok(options.signal instanceof AbortSignal);return new Response(null,{status:path==='/api/refresh'?200:401});},{request:async(name,options,run)=>{assert.equal(name,'drg-auth-refresh');assert.ok(options.signal instanceof AbortSignal);return run();}});
  try{assert.deepEqual(await Promise.all([refreshSession(),refreshSession(),refreshSession()]),[true,true,true]);assert.deepEqual(calls,['/api/me','/api/refresh']);}finally{restore();}
});
test('a temporary account-check failure does not rotate credentials',async()=>{
  const calls=[];
  const restore=environment(async path=>{
    calls.push(path);
    return new Response('temporarily unavailable',{status:503});
  });
  try {
    await assert.rejects(refreshSession(),{status:503});
    assert.deepEqual(calls,['/api/me']);
  } finally { restore(); }
});
test('refresh throttling keeps the retry delay and a later attempt can succeed',async()=>{
  const calls=[];
  const restore=environment(async path=>{
    calls.push(path);
    if(path==='/api/me')return new Response(null,{status:401});
    if(calls.length===2)return Response.json({detail:'Retry later'},{status:429,headers:{'Retry-After':'30'}});
    return new Response(null,{status:200});
  });
  try {
    await assert.rejects(refreshSession(),{status:429,retryAfter:'30',message:'Retry later'});
    assert.equal(await refreshSession(),true);
    assert.deepEqual(calls,['/api/me','/api/refresh','/api/me','/api/refresh']);
  } finally { restore(); }
});
test('stalled refresh releases the shared pending promise and can be tried again',async()=>{
  let stalled=true;
  const restore=environment(async()=>stalled?new Promise(()=>{}):{ok:true},null),normalTimer=globalThis.setTimeout;
  globalThis.setTimeout=(callback,delay,...args)=>normalTimer(callback,delay===30000?10:delay,...args);
  try{await assert.rejects(refreshSession(),error=>error.name==='TimeoutError');stalled=false;assert.equal(await refreshSession(),true);}finally{globalThis.setTimeout=normalTimer;restore();}
});
test('waiting for an inter-tab lock is also bounded',async()=>{
  const restore=environment(async()=>{throw new Error('Must not fetch before the lock');},{request:()=>new Promise(()=>{})}),normalTimer=globalThis.setTimeout;
  globalThis.setTimeout=(callback,delay,...args)=>normalTimer(callback,delay===30000?10:delay,...args);
  try{await assert.rejects(refreshSession(),error=>error.name==='TimeoutError');}finally{globalThis.setTimeout=normalTimer;restore();}
});
