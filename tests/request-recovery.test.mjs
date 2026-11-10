import test from 'node:test';
import assert from 'node:assert/strict';
import {abortable} from '../web/core/request.js';
import {workspaceView,rememberWorkspace,readSession,writeSession} from '../web/core/navigation.js';

test('a stalled response body times out even if the operation ignores abort',async()=>{
  let signal;
  await assert.rejects(abortable(value=>{signal=value;return new Promise(()=>{});},{timeoutMs:10,message:'Timed out'}),error=>error.name==='TimeoutError'&&error.message==='Timed out');
  assert.equal(signal.aborted,true);
});
test('user cancellation settles a stalled decoder and a fresh request still works',async()=>{
  const controller=new AbortController();
  const pending=abortable(()=>new Promise(()=>{}),{signal:controller.signal,timeoutMs:1000});
  controller.abort();await assert.rejects(pending,error=>error.name==='AbortError');
  assert.equal(await abortable(()=>Promise.resolve('sent'),{timeoutMs:100}),'sent');
});
test('an already cancelled operation never starts; normal errors are preserved',async()=>{
  const controller=new AbortController();controller.abort();let starts=0;
  await assert.rejects(abortable(()=>starts++,{signal:controller.signal}),error=>error.name==='AbortError');assert.equal(starts,0);
  const failure=new Error('network');await assert.rejects(abortable(()=>{throw failure;}),error=>error===failure);
});
test('remembered views are per account and invalid values cannot select a module',()=>{
  const data=new Map();globalThis.sessionStorage={getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)};
  rememberWorkspace('alice','chat');rememberWorkspace('bob','files');
  assert.equal(workspaceView('alice'),'chat');assert.equal(workspaceView('bob'),'files');assert.equal(workspaceView('new-account'),'home');
  rememberWorkspace('alice','invalid');assert.equal(workspaceView('alice'),'chat');
  writeSession('drg-workspace-view:alice','https://evil.invalid');assert.equal(workspaceView('alice'),'home');
  writeSession('drg-workspace-view:bob',null);assert.equal(workspaceView('bob'),'home');delete globalThis.sessionStorage;
});
test('blocked session storage does not prevent opening a conversation',()=>{
  globalThis.sessionStorage={getItem(){throw new Error('SecurityError');},setItem(){throw new Error('SecurityError');},removeItem(){throw new Error('SecurityError');}};
  assert.equal(workspaceView('alice'),'home');assert.equal(readSession('room'),null);assert.doesNotThrow(()=>rememberWorkspace('alice','chat'));delete globalThis.sessionStorage;
});

test('phone and desktop remember independent sections, with Messages as phone default',()=>{
  const data=new Map();globalThis.sessionStorage={getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)};
  assert.equal(workspaceView('alice',true),'chat');
  rememberWorkspace('alice','files');rememberWorkspace('alice','notes',true);
  assert.equal(workspaceView('alice'),'files');assert.equal(workspaceView('alice',true),'notes');
  assert.equal(workspaceView('bob',true),'chat');
  writeSession('drg-mobile-view:alice','invalid');assert.equal(workspaceView('alice',true),'chat');
  delete globalThis.sessionStorage;
});
