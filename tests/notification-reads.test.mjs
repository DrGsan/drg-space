import test from 'node:test';
import assert from 'node:assert/strict';
import {applyMessageReads,syncDeliveredReads} from '../web/core/notification-reads.js';

test('cached inbox history follows bounded chat reads without touching other events',()=>{
 const notice=(kind,room,mid)=>({kind,read:false,payload:{space_id:room,message_id:mid}});
 const items=[notice('message','room',10),notice('message','room',11),notice('message','other',10),notice('call','room',10),notice('family_message','telegram:-1',8)];
 applyMessageReads(items,{room:10,'telegram:-1':8});
 assert.equal(items.length,5);assert.deepEqual(items.map(i=>i.read),[true,false,false,false,true]);
 applyMessageReads(items,{room:9});assert.equal(items[0].read,true,'Old deltas cannot unread a notice');
});
test('only our worker receives account-scoped read bounds; no permission or chat read request',async()=>{
 const sent=[],env={location:{origin:'https://note.example'},navigator:{serviceWorker:{async getRegistration(){return {active:{scriptURL:'https://note.example/push-sw.js',postMessage:v=>sent.push(v)}};}}}};
 await syncDeliveredReads('account',{room:10},env);
 assert.deepEqual(sent,[{type:'drg-chat-reads',account_id:'account',reads:{room:10}}]);
 env.navigator.serviceWorker.getRegistration=async()=>({active:{scriptURL:'https://note.example/other.js',postMessage:v=>sent.push(v)}});
 await syncDeliveredReads('account',{room:20},env);assert.equal(sent.length,1);
});
