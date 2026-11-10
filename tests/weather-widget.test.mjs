import test from 'node:test';
import assert from 'node:assert/strict';
import {mountWeather,weatherMarkup} from '../web/modules/dashboard/widgets/weather.js';

const settle=()=>new Promise(resolve=>setImmediate(resolve));
const forecast={configured:true,city:'Москва',forecast_range:'next_24h',updated:1791874800,checked_at:1791878400,forecast_time:1791878400,current:{temperature_2m:12,apparent_temperature:null,weather_code:69,wind_speed_10m:7.2},daily:{temperature_2m_min:[7],temperature_2m_max:[17]}};

function environment(t,{hidden=false}={}) {
  const documentEvents=new Map(),windowEvents=new Map(),timers=new Map();let id=0;
  const document={hidden,addEventListener:(name,fn)=>documentEvents.set(name,fn),removeEventListener:name=>documentEvents.delete(name),createElement:()=>({textContent:'',className:''})};
  const window={addEventListener:(name,fn)=>windowEvents.set(name,fn),removeEventListener:name=>windowEvents.delete(name)};
  for(const [name,value] of Object.entries({document,window})){
    const previous=Object.getOwnPropertyDescriptor(globalThis,name);globalThis[name]=value;
    t.after(()=>{if(previous)Object.defineProperty(globalThis,name,previous);else delete globalThis[name];});
  }
  t.mock.method(globalThis,'setTimeout',(fn,delay)=>{timers.set(++id,{fn,delay});return id;});
  t.mock.method(globalThis,'clearTimeout',key=>timers.delete(key));
  const host={isConnected:true,innerHTML:'',querySelector:()=>null,append:()=>{}};
  return {host,document,documentEvents,windowEvents,timers};
}

test('MET forecast shows its period and attribution without inventing feels-like temperature',async t=>{
  const {host}=environment(t);
  const dispose=await mountWeather(host,{instance:{id:'weather'},api:async()=>forecast});await settle();
  assert.match(host.innerHTML,/Ближайшие 24 часа/);assert.match(host.innerHTML,/Дождь со снегом/);
  assert.match(host.innerHTML,/MET Norway/);assert.match(host.innerHTML,/CC BY 4.0/);
  assert.doesNotMatch(host.innerHTML,/ощущается|NaN/);
  const checked=new Date(forecast.checked_at*1000).toLocaleTimeString('ru',{hour:'2-digit',minute:'2-digit'});
  assert.ok(host.innerHTML.includes('проверено '+checked));dispose();
});

test('weather pauses in a hidden tab and resumes when visible',async t=>{
  const env=environment(t,{hidden:true});let requests=0;
  const dispose=await mountWeather(env.host,{instance:{id:'weather'},api:async()=>{requests++;return forecast;}});
  await settle();assert.equal(requests,0);
  env.document.hidden=false;env.documentEvents.get('visibilitychange')();await settle();assert.equal(requests,1);
  env.document.hidden=true;
  const timer=[...env.timers.values()][0];assert.equal(timer.delay,600000);timer.fn();await settle();assert.equal(requests,1);
  env.document.hidden=false;env.documentEvents.get('visibilitychange')();await settle();assert.equal(requests,2);
  dispose();assert.equal(env.documentEvents.size,0);assert.equal(env.windowEvents.size,0);assert.equal(env.timers.size,0);
});

test('removing a weather widget during a request prevents rendering and timers',async t=>{
  const env=environment(t);let resolve;
  const pending=new Promise(done=>resolve=done);
  const dispose=await mountWeather(env.host,{instance:{id:'weather'},api:()=>pending});dispose();
  resolve(forecast);await settle();assert.match(env.host.innerHTML,/Загружаем прогноз/);assert.doesNotMatch(env.host.innerHTML,/MET Norway/);assert.equal(env.timers.size,0);
  assert.equal(env.documentEvents.size,0);assert.equal(env.windowEvents.size,0);
});

test('weather sizes add forecast information without inventing missing provider values',()=>{
  const rich={...forecast,current:{...forecast.current,relative_humidity:70,pressure_hpa:1017,wind_direction:315,precipitation_mm:0.3,precipitation_hours:6},
    hourly:Array.from({length:6},(_,i)=>({time:forecast.forecast_time+i*10800,temperature_2m:12-i,weather_code:0,wind_speed_10m:7,precipitation_mm:0.2,precipitation_hours:1})),
    forecast_periods:Array.from({length:5},(_,i)=>({start:forecast.forecast_time+i*86400,end:forecast.forecast_time+(i+1)*86400,temperature_2m_min:5+i,temperature_2m_max:15+i,weather_code:3,wind_speed_10m_max:10+i}))};
  const compact=weatherMarkup(rich,'1x1'),wide=weatherMarkup(rich,'2x1'),tall=weatherMarkup(rich,'1x2'),large=weatherMarkup(rich,'2x2');
  assert.doesNotMatch(compact,/weather-hourly|weather-periods|Влажность/);
  assert.match(wide,/Прогноз по часам/);assert.doesNotMatch(wide,/weather-periods/);
  assert.match(tall,/Прогноз на следующие сутки/);assert.doesNotMatch(tall,/weather-hourly/);
  assert.match(large,/weather-hourly/);assert.match(large,/weather-periods/);
  assert.match(large,/96–120 ч/);assert.match(large,/Ветер до 14 км\/ч/);assert.doesNotMatch(tall,/96–120 ч/);
  assert.match(large,/Влажность/);assert.match(large,/1017 гПа/);assert.match(large,/0,3 мм \/ 6 ч/);assert.match(large,/СЗ/);
  const missing=weatherMarkup({...forecast,current:{...forecast.current,relative_humidity:null,pressure_hpa:null,wind_direction:null}},'2x2');
  assert.match(missing,/Почасовой прогноз недоступен/);assert.match(missing,/Прогноз на следующие сутки недоступен/);
  assert.doesNotMatch(missing,/NaN|undefined|Влажность|гПа/);
  const unsafe=weatherMarkup({...rich,city:'<img src=x onerror=alert(1)>',stale:true},'2x2');
  assert.doesNotMatch(unsafe,/<img/);assert.match(unsafe,/&lt;img/);assert.match(unsafe,/Последние доступные данные/);
});
