import {tText,locale} from "../../../core/i18n.js";
import {widgetSize} from '../sizes.js';
export const clockDefaults = Object.freeze({
  mode:'analog', timezone:'local', label:'', theme:'classic',
  show_seconds:true, show_timezone:true, show_date:true, hour_format:'24h',
  date_format:'dd.mm.yyyy', date_template:'DD.MM.YYYY',
  cities:[],
});
export const clockThemes=mode=>mode==='digital'?['classic','minimal','station','segments','flip','accent']:['classic','minimal','station','roman','orbit','bold'];
export const clockTheme=(mode,theme)=>clockThemes(mode).includes(theme)?theme:'classic';
export const clockCapacity=size=>({'1x1':1,'2x1':2,'1x2':2,'2x2':4}[widgetSize(size)]||1);
export const clockCities=config=>config.cities?.length?config.cities.slice(0,4):[{timezone:config.timezone||'local',label:config.label||''}];
export const clockRegions = [
  ['local',tText("dashboard:deviceTimeZone")],
  ['UTC','UTC'],['Europe/Kaliningrad',tText("dashboard:kaliningrad")],['Europe/Moscow',tText("dashboard:moscow")],
  ['Europe/Samara',tText("dashboard:samara")],['Asia/Yekaterinburg',tText("dashboard:yekaterinburg")],['Asia/Omsk',tText("dashboard:omsk")],
  ['Asia/Novosibirsk',tText("dashboard:novosibirsk")],['Asia/Krasnoyarsk',tText("dashboard:krasnoyarsk")],
  ['Asia/Irkutsk',tText("dashboard:irkutsk")],['Asia/Yakutsk',tText("dashboard:yakutsk")],['Asia/Vladivostok',tText("dashboard:vladivostok")],
  ['Asia/Magadan',tText("dashboard:magadan")],['Asia/Kamchatka',tText("dashboard:petropavlovskKamchatsky")],
  ['Europe/London',tText("dashboard:london")],['Europe/Paris',tText("dashboard:paris")],['Europe/Berlin',tText("dashboard:berlin")],
  ['America/New_York',tText("dashboard:newYork")],['America/Chicago',tText("dashboard:chicago")],['America/Los_Angeles',tText("dashboard:losAngeles")],
  ['Asia/Dubai',tText("dashboard:dubai")],['Asia/Kolkata',tText("dashboard:calcutta")],['Asia/Shanghai',tText("dashboard:beijing")],
  ['Asia/Tokyo',tText("dashboard:tokyo")],['Asia/Singapore',tText("dashboard:singapore")],['Australia/Sydney',tText("dashboard:sydney")],['Pacific/Auckland',tText("dashboard:auckland")],
];
export function clockTimezone(timeZone='local'){
  return timeZone==='local'?(Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC'):timeZone;
}
export function clockCity(timeZone){
  const zone=clockTimezone(timeZone);
  return clockRegions.find(([value])=>value===zone)?.[1]||zone.split('/').at(-1).replaceAll('_',' ');
}
const formatters=new Map();
function formatter(timeZone,options){
  timeZone=clockTimezone(timeZone);
  const key=JSON.stringify([timeZone,options]);
  if(!formatters.has(key)){
    if(formatters.size>=80)formatters.delete(formatters.keys().next().value);
    formatters.set(key,new Intl.DateTimeFormat(locale(),{timeZone,...options}));
  }
  return formatters.get(key);
}
const parts=(date,timeZone,options)=>Object.fromEntries(formatter(timeZone,options).formatToParts(date).filter(p=>p.type!=='literal').map(p=>[p.type,p.value]));
export function clockParts(date,timeZone){
  const p=parts(date,timeZone,{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
  return {year:Number(p.year),month:Number(p.month),day:Number(p.day),hour:Number(p.hour),minute:Number(p.minute),second:Number(p.second)};
}
const pad=value=>String(value).padStart(2,'0');
export function clockTime(p,config){
  const hour=config.hour_format==='12h'?(p.hour%12||12):p.hour;
  return `${pad(hour)}:${pad(p.minute)}${config.show_seconds?':'+pad(p.second):''}${config.hour_format==='12h'?(p.hour<12?' AM':' PM'):''}`;
}
export function clockOffset(date,timeZone){
  const offset=parts(date,timeZone,{timeZoneName:'longOffset'}).timeZoneName;
  return offset.replace(/^(GMT|UTC)/,'UTC');
}
export function clockDate(date,config){
  const p=clockParts(date,config.timezone),day=pad(p.day),month=pad(p.month);
  if(config.date_format==='yyyy-mm-dd')return `${p.year}-${month}-${day}`;
  if(config.date_format==='dd/mm/yyyy')return `${day}/${month}/${p.year}`;
  if(config.date_format==='long')return formatter(config.timezone,{day:'numeric',month:'long',year:'numeric'}).format(date);
  if(config.date_format==='weekday')return formatter(config.timezone,{weekday:'long',day:'numeric',month:'long',year:'numeric'}).format(date);
  if(config.date_format==='custom'){
    const long=parts(date,config.timezone,{day:'numeric',month:'long',weekday:'long'}),short=parts(date,config.timezone,{day:'numeric',month:'short',weekday:'short'});
    const tokens={YYYY:String(p.year),YY:String(p.year).slice(-2),MM:month,M:String(p.month),DD:day,D:String(p.day),MMMM:long.month,MMM:short.month,dddd:long.weekday,ddd:short.weekday};
    return config.date_template.replace(/YYYY|MMMM|dddd|MMM|ddd|YY|MM|DD|M|D/g,token=>tokens[token]);
  }
  return `${day}.${month}.${p.year}`;
}
export function clockAngles(p,showSeconds){
  const seconds=showSeconds?p.second:0;
  return {hour:(p.hour%12)*30+p.minute/2+seconds/120,minute:p.minute*6+seconds/10,second:p.second*6};
}

// Local wall-clock policy: day begins at 08:00, night at 20:00.
export const clockPeriod=p=>p.hour>=20||p.hour<8?'night':'day';
