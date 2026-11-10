import test from 'node:test';
import assert from 'node:assert/strict';
import {familyDayKey,familyTimeline} from '../web/modules/family-photos/timeline.js';

test('timeline uses device calendar days across midnight and preserves original viewer indexes',()=>{
 const previous=process.env.TZ;process.env.TZ='America/Los_Angeles';
 try{
  const files=[{id:'new',created:Date.parse('2026-11-01T08:30:00Z')/1000,sender:'Анна'},
   {id:'older',created:Date.parse('2026-11-01T06:30:00Z')/1000,sender:'Иван'},
   {id:'same',created:Date.parse('2026-11-01T07:30:00Z')/1000,sender:'Анна'}];
  const days=familyTimeline(files);
  assert.deepEqual(days.map(d=>[d.key,d.items.map(item=>item.index)]),[['2026-11-01',[0,2]],['2026-10-31',[1]]]);
  assert.deepEqual(familyTimeline(files,'анна').flatMap(d=>d.items.map(item=>item.index)),[0,2]);
  assert.equal(familyDayKey(null),'unknown');assert.equal(familyDayKey('invalid'),'unknown');
 }finally{if(previous===undefined)delete process.env.TZ;else process.env.TZ=previous;}
});

test('search is literal, matches filenames and dates, and does not mutate archive data',()=>{
 const files=[{id:'1',name:'<draft>.png',created:1792929600,sender:'Анна'}, {id:'2',name:'Beach.png',created:1792324800,sender:'Иван'}];
 const original=structuredClone(files);
 assert.deepEqual(familyTimeline(files,'<draft>').flatMap(d=>d.items.map(item=>item.file.id)),['1']);
 assert.equal(familyTimeline(files,'BEACH')[0].items[0].index,1);
 assert.equal(familyTimeline(files,'25')[0].items[0].file.id,'1');
 assert.equal(familyTimeline(files,'missing').length,0);
 assert.deepEqual(files,original);
});
