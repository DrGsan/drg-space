import test from 'node:test';
import assert from 'node:assert/strict';
import {hierarchyRows,issueLanes,sortIssues} from '../web/modules/tracker/view-model.js';
const task=(id,number,extra={})=>({id,number,position:0,kind:'task',parent_id:null,group_id:'group',status:'todo',priority:0,title:id,...extra});
test('filtered descendants retain their complete parent path and sibling subtrees remain together',()=>{
 const parent=task('new-parent',9),child=task('old-child',1,{parent_id:parent.id}),sibling=task('sibling',2),leaf=task('leaf',10,{parent_id:child.id});
 const items=[child,sibling,parent,leaf];const selected=hierarchyRows(items,[leaf]);
 assert.deepEqual(selected.map(r=>[r.item.id,r.context,r.depth]),[['new-parent',true,0],['old-child',true,1],['leaf',false,2]]);
 assert.deepEqual(hierarchyRows(items).map(r=>r.item.id),['sibling','new-parent','old-child','leaf']);
});
test('partial pages preserve orphans, and epic lanes follow manual type through ordinary parents',()=>{
 const epic=task('epic',1,{kind:'epic'}),parent=task('parent',2,{parent_id:epic.id}),child=task('child',3,{parent_id:parent.id}),loose=task('loose',4),orphan=task('orphan',5,{parent_id:'later-page'});
 const items=[epic,parent,child,loose,orphan];const lanes=issueLanes(items,items,[],'epic');
 assert.deepEqual(lanes.map(l=>[l.id,l.items.map(i=>i.id)]),[['epic:epic',['epic','parent','child']],['epic:none',['loose']],['missing:later-page',['orphan']]]);
 assert.equal(hierarchyRows([orphan])[0].missingParent,true);
 assert.equal(parent.kind,'task');
 const groups=issueLanes(items,[child],[{id:'group',name:'Web'},{id:'empty',name:'iOS'}],'group',{empty:true});assert.equal(groups.length,2);
});
test('table sorting is numeric/stable, leaves undated items last and does not mutate source',()=>{
 const a=task('DRG-10',10,{priority:3,due:'2026-11-06'}),b=task('DRG-2',2,{priority:3,due:''}),c=task('DRG-3',3,{priority:1,due:'2026-11-01'});const items=[a,b,c];
 const sort=(field,direction)=>sortIssues(items,field,direction,'en',()=>'',()=> '');
 assert.deepEqual(sort('priority','desc').map(i=>i.number),[2,10,3]);
 assert.deepEqual(sort('title','asc').map(i=>i.number),[2,3,10]);
 assert.deepEqual(sort('due','desc').map(i=>i.number),[10,3,2]);
 assert.deepEqual(items.map(i=>i.number),[10,2,3]);
});
