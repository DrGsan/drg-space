import {locale,tText} from '../../core/i18n.js';

export function familyMediaCount(items){
  const photos=items.filter(({file})=>file.kind==='photo').length;
  const videos=items.filter(({file})=>file.kind==='video').length;
  return [photos?tText('family_photos:photoCount',{count:photos}):'',videos?tText('family_photos:videoCount',{count:videos}):''].filter(Boolean).join(' · ');
}

// Device calendar days, rather than elapsed 24-hour intervals (DST/time zones).
export function familyDayKey(stamp){
  const date=new Date(stamp==null?NaN:Number(stamp)*1000);
  return Number.isFinite(date.getTime())?[date.getFullYear(),String(date.getMonth()+1).padStart(2,'0'),String(date.getDate()).padStart(2,'0')].join('-'):'unknown';
}

export function familyTimeline(files,query=''){
  const needle=query.trim().toLocaleLowerCase(locale());
  const days=new Map();
  files.forEach((file,index)=>{
    const date=new Date(file.created==null?NaN:Number(file.created)*1000);
    const label=Number.isFinite(date.getTime())?date.toLocaleDateString(locale(),{day:'numeric',month:'long',year:'numeric'}):tText('family_photos:unknownDate');
    if(needle&&![file.sender,file.name,label].join(' ').toLocaleLowerCase(locale()).includes(needle))return;
    const key=familyDayKey(file.created);
    if(!days.has(key))days.set(key,{key,label,month:key.slice(0,7),created:file.created,items:[]});
    days.get(key).items.push({file,index});
  });
  return [...days.values()].sort((a,b)=>a.key==='unknown'?1:b.key==='unknown'?-1:b.key.localeCompare(a.key));
}
