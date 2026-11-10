import test from 'node:test';
import assert from 'node:assert/strict';
import {downloadFile} from '../web/modules/files/download.js';

function environment(fetch){
  const original=new Map(['fetch','document','navigator','location'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
  const navigations=[],toasts=[],dialogs=[];
  const ctx={toast:text=>toasts.push(text),modal:html=>{dialogs.push(html);return {querySelector:()=>({})};}};
  for(const [key,value] of Object.entries({fetch,document:{cookie:'drg_csrf=fixture-token'},navigator:{},location:{origin:'https://files.example.com',assign:path=>navigations.push(path)}}))Object.defineProperty(globalThis,key,{value,configurable:true,writable:true});
  return {ctx,navigations,toasts,dialogs,restore(){for(const [key,descriptor] of original)if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}};
}
test('repeated clicks share the check and start one native download without reading a body',async()=>{
  const calls=[];
  const env=environment(async(path,options)=>{calls.push([path,options.method]);assert.ok(options.signal instanceof AbortSignal);return {ok:true,headers:new Headers()};});
  try{
    assert.deepEqual(await Promise.all([downloadFile('/api/files/one/content',env.ctx),downloadFile('/api/files/one/content',env.ctx)]),[true,true]);
    assert.deepEqual(calls,[['/api/files/one/content','HEAD']]);assert.deepEqual(env.navigations,['https://files.example.com/api/files/one/content']);
  }finally{env.restore();}
});
test('permissions failures never refresh or navigate to the API',async()=>{
  const calls=[],env=environment(async(path,options)=>{calls.push(options.method);return {status:403,ok:false};});
  try{assert.equal(await downloadFile('/api/shared-files/link/file/content',env.ctx),false);assert.deepEqual(calls,['HEAD']);assert.deepEqual(env.navigations,[]);assert.match(env.toasts[0],/нет доступа/);}finally{env.restore();}
});
test('a failed refresh offers login and keeps the page',async()=>{
  const calls=[],env=environment(async(path,options)=>{calls.push(path);return {status:401,ok:false};});
  try{
    assert.equal(await downloadFile('/api/files/one/content',env.ctx),false);
    assert.deepEqual(calls,['/api/files/one/content','/api/me','/api/refresh']);
    assert.deepEqual(env.navigations,[]);assert.match(env.dialogs[0],/Войти в аккаунт/);
  }finally{env.restore();}
});
test('network errors keep the page and allow another attempt',async()=>{
  let offline=true;const env=environment(async()=>{if(offline)throw new TypeError('Failed to fetch');return {ok:true,headers:new Headers()};});
  try{
    assert.equal(await downloadFile('/api/files/one/content',env.ctx),false);assert.deepEqual(env.navigations,[]);assert.match(env.toasts[0],/Проверьте соединение/);
    offline=false;assert.equal(await downloadFile('/api/files/one/content',env.ctx),true);assert.equal(env.navigations.length,1);
  }finally{env.restore();}
});

test('download grants are forwarded without buffering the file body',async()=>{
  const started=[];
  const env=environment(async()=>({ok:true,headers:new Headers({'X-DrG-Download-Grant':'fixture-grant'})}));
  env.ctx.onDownloadStarted=value=>started.push(value);
  try{
    assert.equal(await downloadFile('/api/files/one/content',env.ctx),true);
    assert.deepEqual(env.navigations,['https://files.example.com/api/files/one/content?download_grant=fixture-grant']);
    assert.deepEqual(started,['fixture-grant']);
  }finally{env.restore();}
});
