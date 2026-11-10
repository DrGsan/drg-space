import {test} from 'node:test';
import assert from 'node:assert/strict';
import {clockDefaults,clockTimezone,clockCity,clockParts,clockTime,clockDate,clockOffset,clockAngles,clockPeriod,clockCities,clockCapacity,clockTheme,clockThemes} from '../web/modules/dashboard/widgets/clock-format.js';

test('world clock capacity keeps configured cities intact and preserves legacy single clocks',()=>{
 const cities=[{timezone:'local',label:'Home'},{timezone:'Asia/Vladivostok'},{timezone:'UTC'},{timezone:'Europe/Paris'}],config={...clockDefaults,cities};
 for(const [size,count] of [['1x1',1],['compact',1],['wide',2],['2x1',2],['1x2',2],['2x2',4]]){assert.equal(clockCapacity(size),count);assert.equal(clockCities(config).length,4);}
 assert.deepEqual(clockCities({timezone:'Europe/Moscow',label:'MSK'}),[{timezone:'Europe/Moscow',label:'MSK'}]);
 assert.equal(clockThemes('analog').length,6);assert.equal(clockThemes('digital').length,6);
 assert.equal(clockTheme('digital','roman'),'classic');assert.equal(clockTheme('analog','flip'),'classic');assert.equal(clockTheme('digital','station'),'station');
});

test('automatic clocks follow the device for time, date and offset without changing explicit cities',()=>{
 const zone=Intl.DateTimeFormat().resolvedOptions().timeZone,date=new Date('2026-10-11T20:59:58Z');
 assert.equal(clockDefaults.timezone,'local');assert.equal(clockTimezone('local'),zone);
 assert.deepEqual(clockParts(date,'local'),clockParts(date,zone));
 assert.equal(clockDate(date,clockDefaults),clockDate(date,{...clockDefaults,timezone:zone}));
 assert.equal(clockOffset(date,'local'),clockOffset(date,zone));
 assert.equal(clockCity('local'),clockCity(zone));
 assert.equal(clockTimezone('Europe/Moscow'),'Europe/Moscow');
 assert.equal(clockCity('Asia/Vladivostok'),'Владивосток');
});

test('Moscow and Vladivostok show distinct local times and dates for one instant',()=>{
 const date=new Date('2026-10-11T20:59:58Z');
 const m=clockParts(date,'Europe/Moscow'),v=clockParts(date,'Asia/Vladivostok');
 assert.equal(clockTime(m,clockDefaults),'23:59:58');assert.equal(clockTime(v,clockDefaults),'06:59:58');
 assert.equal(clockDate(date,{...clockDefaults,timezone:'Europe/Moscow'}),'11.10.2026');
 assert.equal(clockDate(date,{...clockDefaults,timezone:'Asia/Vladivostok',date_format:'yyyy-mm-dd'}),'2026-10-12');
 assert.equal(clockOffset(date,'Europe/Moscow'),'UTC+03:00');assert.equal(clockOffset(date,'Asia/Vladivostok'),'UTC+10:00');
});
test('midnight is 00 rather than 24 and 12-hour mode handles noon and midnight',()=>{
 const midnight=clockParts(new Date('2026-10-11T21:00:00Z'),'Europe/Moscow');
 assert.equal(clockTime(midnight,{...clockDefaults,show_seconds:false}),'00:00');
 assert.equal(clockTime(midnight,{...clockDefaults,hour_format:'12h',show_seconds:false}),'12:00 AM');
 assert.equal(clockTime({...midnight,hour:12},{...clockDefaults,hour_format:'12h'}),'12:00:00 PM');
});
test('IANA zones handle spring and autumn DST transitions and fractional-hour offsets',()=>{
 const springA=clockParts(new Date('2026-03-08T06:59:59Z'),'America/New_York'),springB=clockParts(new Date('2026-03-08T07:00:00Z'),'America/New_York');
 assert.equal(clockTime(springA,clockDefaults),'01:59:59');assert.equal(clockTime(springB,clockDefaults),'03:00:00');
 const fallA=new Date('2026-11-01T05:59:59Z'),fallB=new Date('2026-11-01T06:00:00Z');
 assert.equal(clockTime(clockParts(fallA,'America/New_York'),clockDefaults),'01:59:59');assert.equal(clockTime(clockParts(fallB,'America/New_York'),clockDefaults),'01:00:00');
 assert.equal(clockOffset(fallA,'America/New_York'),'UTC-04:00');assert.equal(clockOffset(fallB,'America/New_York'),'UTC-05:00');
 assert.equal(clockTime(clockParts(new Date('2026-10-11T00:00:00Z'),'Asia/Kolkata'),clockDefaults),'05:30:00');
});
test('date presets and custom tokens use the selected region and never execute markup',()=>{
 const d=new Date('2026-10-11T20:00:00Z'),config={...clockDefaults,timezone:'Asia/Vladivostok'};
 assert.equal(clockDate(d,{...config,date_format:'dd/mm/yyyy'}),'12/10/2026');
 assert.match(clockDate(d,{...config,date_format:'long'}),/^12 октября 2026/);
 assert.match(clockDate(d,{...config,date_format:'weekday'}),/^понедельник/);
 assert.equal(clockDate(d,{...config,date_format:'custom',date_template:'ddd · DD.MM.YYYY'}),'пн · 12.10.2026');
 assert.equal(clockDate(d,{...config,date_format:'custom',date_template:'<script>hi</script> DD'}),'<script>hi</script> 12');
});
test('hands account for minutes and optional seconds at noon and midnight',()=>{
 const p={hour:3,minute:30,second:30};
 assert.deepEqual(clockAngles(p,true),{hour:105.25,minute:183,second:180});
 assert.deepEqual(clockAngles(p,false),{hour:105,minute:180,second:180});
 assert.deepEqual(clockAngles({hour:0,minute:0,second:0},true),{hour:0,minute:0,second:0});
});

test('regional night follows local boundaries, independently for each clock',()=>{
 const instant=new Date('2026-10-11T12:00:00Z');
 assert.equal(clockPeriod(clockParts(instant,'Europe/Moscow')),'day');
 assert.equal(clockPeriod(clockParts(instant,'Asia/Vladivostok')),'night');
 const morning=new Date('2026-10-11T22:00:00Z');
 assert.equal(clockPeriod(clockParts(morning,'Europe/Moscow')),'night');
 assert.equal(clockPeriod(clockParts(morning,'Asia/Vladivostok')),'day');
 for(const [hour,minute,period] of [[7,59,'night'],[8,0,'day'],[19,59,'day'],[20,0,'night'],[0,0,'night']])assert.equal(clockPeriod({hour,minute}),period);
});
