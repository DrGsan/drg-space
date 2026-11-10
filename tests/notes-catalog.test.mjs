import test from 'node:test';
import assert from 'node:assert/strict';
import {watchNoteCatalog} from '../web/modules/notes/catalog-refresh.js';

const flush=()=>new Promise(resolve=>setTimeout(resolve,0));
function surfaces(){globalThis.window=new EventTarget();globalThis.document=Object.assign(new EventTarget(),{hidden:false});}

test('catalog coalesces focus and online refreshes and fetches metadata only',async()=>{
 surfaces();const calls=[],state={notes:[]};let resolveNotes;const applied=[];
 const stop=watchNoteCatalog({state,api(path){calls.push(path);return path==='/social'?Promise.resolve({spaces:['friends']}):new Promise(resolve=>resolveNotes=resolve);}},(...data)=>applied.push(data));
 try{
  window.dispatchEvent(new Event('focus'));window.dispatchEvent(new Event('online'));
  assert.deepEqual(calls,['/notes?catalog=true&catalog_revision=','/social']);
  resolveNotes([{id:'shared'}]);await flush();assert.deepEqual(applied,[[[{id:'shared'}],['friends']]]);
 }finally{stop();}
});

test('unchanged revision retains notes while workspace changes remain visible',async()=>{
 surfaces();const notes=[{id:'kept'}],state={notes},applied=[],calls=[];let turn=0;
 const stop=watchNoteCatalog({state,api(path){calls.push(path);if(path==='/social')return Promise.resolve({spaces:['new-space']});return Promise.resolve(++turn===1?{revision:'r1',unchanged:false,notes}:{revision:'r1',unchanged:true});}},(...value)=>applied.push(value));
 try{
  window.dispatchEvent(new Event('focus'));await flush();window.dispatchEvent(new Event('focus'));await flush();
  assert.equal(calls[2],'/notes?catalog=true&catalog_revision=r1');assert.equal(applied[1][0],notes);assert.deepEqual(applied[1][1],['new-space']);
 }finally{stop();}
});

test('discarded response does not advance revision past local changes',async()=>{
 surfaces();const state={notes:[]},calls=[];let finish;
 const stop=watchNoteCatalog({state,api(path){calls.push(path);return path==='/social'?Promise.resolve({spaces:[]}):new Promise(resolve=>finish=resolve);}},()=>{});
 try{
  window.dispatchEvent(new Event('focus'));state.notes.push({id:'local'});finish({revision:'discarded',unchanged:false,notes:[]});await flush();
  window.dispatchEvent(new Event('focus'));assert.equal(calls[2],'/notes?catalog=true&catalog_revision=');finish({revision:'accepted',unchanged:false,notes:[]});await flush();
 }finally{stop();}
});
test('hidden views pause catalog work and a destroyed workspace ignores pending data',async()=>{
 surfaces();const state={notes:[]},calls=[];let finish;const applied=[];
 const stop=watchNoteCatalog({state,api(path){calls.push(path);return path==='/social'?Promise.resolve({spaces:[]}):new Promise(resolve=>finish=resolve);}},data=>applied.push(data));
 document.hidden=true;window.dispatchEvent(new Event('focus'));assert.deepEqual(calls,[]);
 document.hidden=false;document.dispatchEvent(new Event('visibilitychange'));stop();finish([{id:'late'}]);await flush();assert.deepEqual(applied,[]);
 window.dispatchEvent(new Event('focus'));assert.equal(calls.length,2);
});
test('a local catalog mutation wins over an older background request',async()=>{
 surfaces();const state={notes:[]};let finish;const applied=[];
 const stop=watchNoteCatalog({state,api:path=>path==='/social'?Promise.resolve({spaces:[]}):new Promise(resolve=>finish=resolve)},data=>applied.push(data));
 try{window.dispatchEvent(new Event('focus'));state.notes=[{id:'new-local-page'}];finish([]);await flush();assert.deepEqual(applied,[]);}finally{stop();}
});

test('an in-place local creation also wins over an older request',async()=>{
 surfaces();const state={notes:[]};let finish;const applied=[];
 const stop=watchNoteCatalog({state,api:path=>path==='/social'?Promise.resolve({spaces:[]}):new Promise(resolve=>finish=resolve)},data=>applied.push(data));
 try{window.dispatchEvent(new Event('focus'));state.notes.push({id:'created-now'});finish([]);await flush();assert.deepEqual(applied,[]);}finally{stop();}
});
