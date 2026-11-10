import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {seasonalThemeForDate,nextSeasonalDateCheck} from '../web/core/seasonal-calendar.js';

const themes=JSON.parse(readFileSync(new URL('../app/seasonal_themes.json',import.meta.url),'utf8'));
test('holiday particles are explicit, bounded and never fall back to snow',async()=>{
  const {seasonalParticles,seasonalParticleLimit}=await import('../web/core/seasonal-particles.js');
  const expected={newyear:'snow',space:'stars',starwars:'stars',valentine:'hearts',hugs:'hearts',kindness:'hearts',spring:'petals',halloween:'leaves',music:'notes',birthday:'confetti'};
  for(const theme of themes){
    assert.equal(theme.particles,expected[theme.id]||'none',theme.id);
    const markup=seasonalParticles(theme.particles,390,844);
    assert.equal((markup.match(/class="seasonal-ambient"/g)||[]).length,theme.particles==='none'?0:seasonalParticleLimit);
    if(markup)assert.match(markup,new RegExp('data-particle-kind="'+theme.particles+'"'));
  }
  assert.equal(seasonalParticles('unknown',390,844),'');
  assert.equal(seasonalParticles(undefined,390,844),'');
});
test('twenty distinct holidays have translated labels and inclusive local date boundaries',()=>{
  assert.equal(themes.length,20);assert.equal(new Set(themes.map(x=>x.id)).size,20);
  for(const language of ['ru','en']){
    const locale=JSON.parse(readFileSync(new URL('../locales/'+language+'/appearance.json',import.meta.url),'utf8'));
    for(const theme of themes)assert.ok(locale[theme.name_key],language+' '+theme.id);
  }
  for(const [month,day,expected] of [[10,30,''],[10,31,'halloween'],[11,1,''],[12,19,''],[12,20,'newyear'],[12,31,'newyear'],[1,1,'newyear'],[1,10,'newyear'],[1,11,''],[2,13,''],[2,14,'valentine'],[2,15,''],[3,7,''],[3,8,'spring'],[3,9,''],[4,11,''],[4,12,'space'],[4,13,'']]){
    for(const [h,m,s,ms] of [[0,0,0,0],[23,59,59,999]])assert.equal(seasonalThemeForDate(themes,new Date(2026,month-1,day,h,m,s,ms)),expected,`${month}/${day} ${h}`);
  }
});
test('one UTC instant selects different holiday dates on east and west devices',()=>{
  const code=`import {readFileSync} from 'node:fs';import {seasonalThemeForDate} from './web/core/seasonal-calendar.js';console.log(seasonalThemeForDate(JSON.parse(readFileSync('app/seasonal_themes.json')),new Date('2026-10-30T22:30:00Z')));`;
  const read=zone=>execFileSync(process.execPath,['--input-type=module','-e',code],{env:{...process.env,TZ:zone},encoding:'utf8'}).trim();
  assert.equal(read('Europe/Moscow'),'halloween');assert.equal(read('America/New_York'),'');
});
test('next check follows local midnight through 23/25 hour DST days and never spins',()=>{
  const code=`import {nextSeasonalDateCheck} from './web/core/seasonal-calendar.js';console.log(JSON.stringify([nextSeasonalDateCheck(new Date(2026,2,8)),nextSeasonalDateCheck(new Date(2026,10,1))]));`;
  const result=JSON.parse(execFileSync(process.execPath,['--input-type=module','-e',code],{env:{...process.env,TZ:'America/New_York'},encoding:'utf8'}));
  assert.deepEqual(result,[23*3600000+100,25*3600000+100]);
  assert.equal(nextSeasonalDateCheck(new Date(2026,11,31,23,59,59,999)),1000);
  assert.equal(nextSeasonalDateCheck(new Date(2026,11,31,23,59,0)),60100);
});

test('movable dates, birthday priority and event dates differ from decorative windows',async()=>{
 const {seasonalDate,seasonalThemesForDay,sortedSeasonalThemes}=await import('../web/core/seasonal-calendar.js');
 const theme=id=>themes.find(x=>x.id===id);
 for(const year of [2026,2027,2028,2029,2030,2031,2032]){
   const beer=seasonalDate(theme('beer'),year);assert.equal(beer.getDay(),5);assert.equal(beer.getMonth(),7);assert.ok(beer.getDate()<=7);assert.equal(seasonalThemeForDate(themes,beer),'beer');
   const programmer=seasonalDate(theme('programmers'),year);assert.equal(programmer.getMonth(),8);assert.equal(programmer.getDate(),year%4===0?12:13);assert.equal(seasonalThemeForDate(themes,programmer),'programmers');
 }
 assert.equal(seasonalThemeForDate(themes,new Date(2026,11,28)),'newyear');
 assert.equal(seasonalThemeForDate(themes,new Date(2026,11,29)),'birthday');
 assert.equal(seasonalThemeForDate(themes,new Date(2026,11,30)),'newyear');
 assert.deepEqual(seasonalThemesForDay(themes,new Date(2026,11,20)),[]);
 assert.deepEqual(seasonalThemesForDay(themes,new Date(2026,0,1)).map(t=>t.id),['newyear']);
 const sorted=sortedSeasonalThemes(themes,2026).map(t=>t.id);assert.equal(sorted.at(-2),'newyear');assert.equal(sorted.at(-1),'birthday');
 for(const lang of ['ru','en']){const phrases=JSON.parse(readFileSync(`locales/${lang}/dashboard.json`,'utf8'));for(const theme of themes)assert.ok(phrases[theme.phrase_key]);}
 const {holidaySymbol,holidayScene,seasonalArtworkIds}=await import('../web/core/seasonal-art.js');
 assert.equal(seasonalArtworkIds.length,15);
 for(const theme of themes){assert.match(holidaySymbol(theme.id),/<(?:path|rect|circle|g)\b/);assert.match(holidayScene(theme.id),/<(?:path|rect|svg)\b/);}
});

test('greeting follows preview immediately, falls back to calendar and spans the New Year frame',async()=>{
 const {seasonalGreetingTheme}=await import('../web/core/seasonal-calendar.js');
 const ordinary=new Date(2026,10,10,12);
 assert.equal(seasonalGreetingTheme(themes,ordinary),undefined);
 for(const theme of themes)assert.equal(seasonalGreetingTheme(themes,ordinary,theme.id)?.phrase_key,theme.phrase_key);
 assert.equal(seasonalGreetingTheme(themes,ordinary,'invalid'),undefined);
 assert.equal(seasonalGreetingTheme(themes,new Date(2026,11,20))?.id,'newyear');
 assert.equal(seasonalGreetingTheme(themes,new Date(2026,11,29))?.id,'birthday');
 assert.equal(seasonalGreetingTheme(themes,new Date(2026,11,29),'starwars')?.id,'starwars');
 assert.equal(seasonalGreetingTheme(themes,new Date(2026,11,29),'')?.id,'birthday');
});
