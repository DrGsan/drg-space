import {test} from 'node:test';
import assert from 'node:assert/strict';
import {loadProductionCalendar} from '../web/core/calendar-client.js';
import {calendarEvents,eventsOnDay,upcomingCalendarEvents} from '../web/core/calendar-events.js';
import {readFileSync} from 'node:fs';
const themes=JSON.parse(readFileSync('app/seasonal_themes.json','utf8'));
test('many widgets share one request and respect server next-check seconds',async()=>{
 let requests=0,resolve;const gate=new Promise(r=>resolve=r),now=1800000000000;
 const api=async()=>{requests++;await gate;return {events:[],next_check_at:now/1000+86400};};
 const promises=Array.from({length:100},()=>loadProductionCalendar(api,2040,now));assert.equal(requests,1);resolve();await Promise.all(promises);
 await loadProductionCalendar(api,2040,now+60000);assert.equal(requests,1);
 await loadProductionCalendar(api,2040,now+86400001);assert.equal(requests,2);
});
test('civil intervals retain exclusive ends and thematic events never create days off',()=>{
 const source={events:[{start:'2028-12-30',end:'2029-01-03',title:'New year',kind:'holiday'},{start:'2028-11-04',end:'2028-11-05',title:'Unity',kind:'holiday'}]};
 const events=calendarEvents(2028,source,themes);
 assert.equal(eventsOnDay(events,'2028-12-29')[0].id,'birthday');
 assert.equal(eventsOnDay(events,'2028-12-29')[0].kind,'thematic');
 assert.ok(eventsOnDay(events,'2029-01-02').some(e=>e.title==='New year'));
 assert.equal(eventsOnDay(events,'2029-01-03').length,0);
 assert.equal(eventsOnDay(events,'2028-09-12')[0].id,'programmers');
 assert.deepEqual(calendarEvents(2028,source,themes,{production:false,thematic:false}),[]);
 assert.ok(calendarEvents(2028,source,themes,{thematic:false}).every(e=>e.kind!=='thematic'));
 assert.equal(upcomingCalendarEvents(events,'2028-11-04')[0].title,'Unity');
});
test('failed network requests have client backoff and keep last good data visibly stale',async()=>{
 let count=0;const api=async()=>{count++;throw Error('offline');};
 await assert.rejects(loadProductionCalendar(api,2045,1000000));
 await assert.rejects(loadProductionCalendar(api,2045,1000001));assert.equal(count,1);
 await assert.rejects(loadProductionCalendar(api,2045,1060001));assert.equal(count,2);
 const good=async()=>({events:[{title:'saved'}],next_check_at:1});
 await loadProductionCalendar(good,2046,1000000);
 const stale=await loadProductionCalendar(api,2046,1060001);
 assert.equal(stale.status,'stale');assert.equal(stale.events[0].title,'saved');
});
