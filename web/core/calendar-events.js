import {seasonalDate} from './seasonal-calendar.js';
export function civilKey(date){return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;}
export function calendarEvents(year,source,themes,{production=true,thematic=true}={}){
  const events=production?(source?.events||[]).map(event=>({...event,id:`source:${event.start}:${event.end}:${event.title}`})):[];
  if(thematic)for(const theme of themes){const date=seasonalDate(theme,year),end=new Date(year,date.getMonth(),date.getDate()+1,12);events.push({id:theme.id,start:civilKey(date),end:civilKey(end),name_key:theme.name_key,symbol:theme.symbol,kind:'thematic'});}
  return events.sort((a,b)=>a.start.localeCompare(b.start)||a.id.localeCompare(b.id));
}
export function eventsOnDay(events,key){return events.filter(event=>event.start<=key&&event.end>key);}
// Weekend ranges remain marked in the grid; the agenda highlights named dates.
export function upcomingCalendarEvents(events,from,limit=4){return events.filter(event=>event.kind!=='day_off'&&event.end>from).slice(0,limit);}
