import test from 'node:test';
import assert from 'node:assert/strict';
import * as Y from 'yjs';
import {documentUpdates} from '../web/modules/notes/document-sync.js';
const tick=()=>new Promise(resolve=>setTimeout(resolve,5));

test('fast typing produces small deltas and coalesces while awaiting an ACK',async()=>{
 const doc=new Y.Doc();doc.getText('body').insert(0,'x'.repeat(50000));
 const initial=Y.encodeStateAsUpdate(doc),messages=[],receiver=new Y.Doc();Y.applyUpdate(receiver,initial);
 const queue=documentUpdates(doc,(update,seq)=>{messages.push({update,seq});return true;},{delay:1});queue.sync(initial);
 doc.getText('body').insert(50000,'One');queue.flush();
 for(const word of [' two',' three',' four']){doc.getText('body').insert(doc.getText('body').length,word);queue.flush();}
 assert.equal(messages.length,1);assert.ok(messages[0].update.length<initial.length/100);
 queue.ack(messages[0].seq);await tick();assert.equal(messages.length,2);assert.ok(messages[1].update.length<initial.length/100);
 for(const message of messages)Y.applyUpdate(receiver,message.update);
 assert.equal(receiver.getText('body').toString(),doc.getText('body').toString());queue.ack(messages[1].seq);assert.equal(queue.dirty,false);
 queue.destroy();doc.destroy();receiver.destroy();
});

test('reconnect preserves edits even when the last ACK was lost',()=>{
 const doc=new Y.Doc(),server=new Y.Doc(),messages=[];const queue=documentUpdates(doc,(update,seq)=>{messages.push({update,seq});return true;});queue.sync(Y.encodeStateAsUpdate(server));
 doc.getText('body').insert(0,'Saved');queue.flush();Y.applyUpdate(server,messages[0].update);
 queue.offline();doc.getText('body').insert(5,' and offline');queue.flush();assert.equal(messages.length,1);
 const canonical=Y.encodeStateAsUpdate(server);Y.applyUpdate(doc,canonical,'remote');queue.sync(canonical);queue.flush();
 assert.equal(messages.length,2);Y.applyUpdate(server,messages[1].update);queue.ack(messages[1].seq);
 assert.equal(server.getText('body').toString(),'Saved and offline');assert.equal(queue.dirty,false);queue.destroy();doc.destroy();server.destroy();
});
