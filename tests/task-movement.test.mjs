import {test} from 'node:test';
import assert from 'node:assert/strict';
import {movePlan,siblingOrder,taskMoveRevision} from '../web/modules/tasks/move.js';
const tasks=[{id:'Aaaaaaaa',list_id:'list',parent_id:null,position:2,version:3},{id:'bbbbbbbb',list_id:'list',parent_id:'Aaaaaaaa',version:1},{id:'cccccccc',list_id:'list',parent_id:null,position:3,version:1},{id:'foreign1',list_id:'other',parent_id:null,version:1}];
test('move revision is identical to Python and Swift, excludes foreign lists, and detects edits',async()=>{
 assert.equal(await taskMoveRevision(tasks,'list'),'a55a739802580df8bb279d5a6bb490b369b13855650864d87a4c20ce7b5fcbeb');
 assert.equal(await taskMoveRevision(tasks.slice(0,2),'list'),'82cf79570dda35d09ce3f01d41768903b665872b8722fed8722941c591d8e0d2');
 assert.equal(await taskMoveRevision([...tasks].reverse(),'list'),await taskMoveRevision(tasks,'list'));
 assert.notEqual(await taskMoveRevision(tasks.map(t=>({...t,version:t.version+1})),'list'),await taskMoveRevision(tasks,'list'));
});
test('nesting and extraction preserve two levels; parents cannot move into descendants or other lists',()=>{
 assert.equal(movePlan(tasks,'Aaaaaaaa','cccccccc','inside'),null);
 assert.equal(movePlan(tasks,'Aaaaaaaa','bbbbbbbb','before'),null);
 assert.equal(movePlan(tasks,'cccccccc','bbbbbbbb','inside'),null);
 assert.equal(movePlan(tasks,'bbbbbbbb','foreign1','before'),null);
 assert.deepEqual(movePlan(tasks,'bbbbbbbb','cccccccc','inside'),{parent_id:'cccccccc',before_id:null,target:'cccccccc',zone:'inside'});
 assert.equal(movePlan(tasks,'bbbbbbbb','Aaaaaaaa','before').parent_id,null);
 assert.equal(movePlan(tasks,'cccccccc','Aaaaaaaa','after').before_id,null);
});
test('sibling ordering has a stable legacy fallback',()=>{
 const rows=[{id:'b',created:1},{id:'a',created:1},{id:'c',position:1,created:0}];
 assert.deepEqual(rows.sort(siblingOrder).map(t=>t.id),['a','b','c']);
});
