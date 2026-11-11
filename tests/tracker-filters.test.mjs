import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeFilters,matchesIssue} from '../web/modules/tracker/filters.js';
import {boardOrder} from '../web/modules/tracker/view-model.js';
import {TrackerTransport} from '../web/modules/tracker/transport.js';

test('facets OR inside, AND across, ordinary is distinct from low and mine excludes remote homonyms',()=>{
 const filters={...normalizeFilters(),statuses:['todo','work'],priorities:[0,3],mine:true,unfinished:true,query:'photo'};
 const item={id:'one',title:'Photo upload',key:'DRG-1',status:'todo',priority:0,assignee_id:'me',assignee_peer:''};
 assert.equal(matchesIssue(item,filters,'me'),true);
 for(const changes of [{priority:1},{status:'review'},{status:'done'},{assignee_peer:'remote'},{title:'Different'}])assert.equal(matchesIssue({...item,...changes},filters,'me'),false);
 assert.deepEqual(normalizeFilters({statuses:['todo','invalid','todo'],priorities:[0,1,'3',false],mine:'yes',group:'../../etc'}),{query:'',mine:false,unfinished:false,statuses:['todo'],priorities:[0,1],group:null});
});
test('board chronology ignores positions/ordinary edits and uses stable newest-first ties',()=>{
 const items=[{id:'a',number:1,status:'todo',created:10,updated:900,position:900},{id:'b',number:2,status:'todo',created:20,updated:20,position:0},{id:'c',number:3,status:'todo',created:20,position:800}];
 assert.deepEqual([...items].sort(boardOrder).map(i=>i.id),['c','b','a']);
 assert.deepEqual(items.map(i=>({...i,status:'work',status_entered_at:50-i.number})).sort(boardOrder).map(i=>i.id),['a','b','c']);
});
test('complete projection reaches later pages and retries changing project versions',async()=>{
 const transport=new TrackerTransport({});let calls=0;
 transport.issues=async(_project,after)=>{
  calls++;const version=calls===1?1:2;
  return {items:[{id:String(after),number:after+1}],more:after<2,cursor:after+1,project:{version},view_counts:{todo:3,work:0,review:0,done:0}};
 };
 const page=await transport.allIssues({id:'project'});
 assert.deepEqual(page.items.map(i=>i.number),[1,2,3]);assert.equal(calls,5);assert.equal(page.more,false);
});

test('cancelled is independently filterable and excluded from unfinished work',()=>{
 const cancelled={id:'cancelled',title:'Cancelled',status:'cancelled',priority:0};
 assert.equal(matchesIssue(cancelled,normalizeFilters({unfinished:true}),'owner'),false);
 assert.equal(matchesIssue(cancelled,normalizeFilters({statuses:['cancelled']}),'owner'),true);
 assert.equal(matchesIssue({...cancelled,status:'done'},normalizeFilters({statuses:['cancelled']}),'owner'),false);
});
