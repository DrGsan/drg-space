import {tText,tHtml,locale} from '../../../core/i18n.js';
import {esc,dayKey,today} from '../../../core/ui.js';
import themes from '../../../../app/seasonal_themes.json';
import {loadProductionCalendar} from '../../../core/calendar-client.js';
import {calendarEvents,eventsOnDay,upcomingCalendarEvents} from '../../../core/calendar-events.js';
import {widgetSize} from '../sizes.js';
export function mountCalendar(host,ctx){
  const {state,navigate}=ctx,config={production:true,thematic:true,...ctx.instance?.config},size=widgetSize(ctx.instance?.size),compact=size==='1x1',upcoming=size==='1x2'||size==='2x2',hasTasks=ctx.moduleEnabled('tasks');
  let disposed=false,month=new Date(state.month),selected=state.day||today(),source=null,loadedYear=null,request=0;
  const title=event=>event.name_key?tText('appearance:'+event.name_key):event.name?tText('dashboard:'+event.name):event.title;
  const dateText=key=>new Date(key+'T12:00:00').toLocaleDateString(locale(),{day:'numeric',month:'long'});
  const openReminders=()=>{state.day=selected;state.tasksFocusDay=selected;navigate('tasks');};
  function draw(){
    if(disposed)return;
    const y=month.getFullYear(),m=month.getMonth(),start=(new Date(y,m,1).getDay()+6)%7,total=new Date(y,m+1,0).getDate(),events=calendarEvents(y,loadedYear===y?source:null,themes,config),chosen=eventsOnDay(events,selected),tasks=hasTasks?state.tasks.filter(t=>t.day===selected).slice().sort((a,b)=>Number(a.done)-Number(b.done)):[],upcomingFrom=selected.slice(0,7)===dayKey(month).slice(0,7)?selected:dayKey(new Date(y,m,1)),next=upcomingCalendarEvents(events,upcomingFrom);
    host.innerHTML=`<div class="holiday-calendar" data-calendar-size="${size}"><div class="calendar-overview"><div class="calendar-month"><div class="calendar-head"><h3>${esc(month.toLocaleDateString(locale(),{month:'long',year:'numeric'}))}</h3><div><button class="icon-btn" data-shift="-1" aria-label="${tHtml('dashboard:previousMonth')}">‹</button><button class="icon-btn" data-shift="1" aria-label="${tHtml('dashboard:nextMonth')}">›</button></div></div><div class="calendar-grid">${['mon','tue','wed','thu','fri','sat','sun'].map(key=>`<span class="weekday">${tHtml('dashboard:'+key)}</span>`).join('')}${'<span></span>'.repeat(start)}${Array.from({length:total},(_,i)=>{
      const key=dayKey(new Date(y,m,i+1)),dayEvents=eventsOnDay(events,key),off=dayEvents.some(e=>e.kind==='holiday'||e.kind==='day_off'),short=dayEvents.some(e=>e.kind==='short_day'),fun=dayEvents.some(e=>e.kind==='thematic'),task=hasTasks&&state.tasks.some(t=>t.day===key&&!t.done);
      return `<button class="calendar-day ${key===today()?'today':''} ${key===selected?'selected':''} ${off?'calendar-off':''} ${short?'calendar-short':''}" data-day="${key}" aria-pressed="${key===selected}" aria-label="${esc([dateText(key),...dayEvents.map(title)].join(', '))}"><span>${i+1}</span><span class="calendar-markers">${fun?'<b></b>':''}${task?'<i></i>':''}</span></button>`;
    }).join('')}</div></div><section class="calendar-agenda" aria-live="polite"><h3>${esc(dateText(selected))}</h3><div class="calendar-event-list">${chosen.map(e=>`<p class="calendar-event ${e.kind}">${e.symbol?`<span aria-hidden="true">${esc(e.symbol)}</span>`:''}<span>${esc(title(e))}</span></p>`).join('')||`<p class="hint">${tHtml('dashboard:calendarNothing')}</p>`}</div>${!compact&&hasTasks?`<div class="calendar-agenda-list">${tasks.slice(0,4).map(t=>`<button class="recent-note ${t.done?'done':''}" data-reminders><span>${t.done?'✓':'○'}</span><strong>${esc(t.title)}</strong></button>`).join('')||`<p class="hint">${tHtml('dashboard:noRemindersForTheSelectedDay')}</p>`}</div><button class="text-link" data-reminders>${tHtml('dashboard:allReminders2')}</button>`:''}</section></div>${upcoming?`<section class="calendar-upcoming"><h3>${tHtml('dashboard:calendarUpcoming')}</h3>${next.map(e=>`<button data-event-day="${e.start}" class="text-link"><time datetime="${e.start}">${esc(dateText(e.start))}</time><span>${e.symbol?esc(e.symbol)+' ':''}${esc(title(e))}</span></button>`).join('')}</section>`:''}${config.production&&!compact?`<p class="calendar-source hint"><a href="https://ovodov.me/trud" target="_blank" rel="noopener noreferrer">${tHtml('dashboard:calendarSource')}</a>${loadedYear===y&&(source?.status==='stale'||!source?.has_data)?`<span role="status">${tHtml('dashboard:'+(source?.has_data?'calendarSourceStale':'calendarSourceUnavailable'))}</span>`:''}</p>`:''}</div>`;
    host.querySelectorAll('[data-day]').forEach(button=>button.onclick=()=>{selected=button.dataset.day;draw();});
    host.querySelectorAll('[data-reminders]').forEach(button=>button.onclick=openReminders);
    host.querySelectorAll('[data-event-day]').forEach(button=>button.onclick=()=>{selected=button.dataset.eventDay;month=new Date(selected+'T12:00:00');draw();});
    host.querySelectorAll('[data-shift]').forEach(button=>button.onclick=()=>{const direction=Number(button.dataset.shift);month=new Date(y,m+direction,1);selected=dayKey(month);draw();load();if(!document.hidden&&!matchMedia('(prefers-reduced-motion: reduce)').matches)host.querySelector('.calendar-grid').animate([{opacity:0,transform:`translateX(${direction*10}px)`},{opacity:1,transform:'none'}],{duration:300,easing:'cubic-bezier(.22,1,.36,1)'});});
  }
  function load(){
    if(!config.production)return;
    const year=month.getFullYear(),ticket=++request;
    loadProductionCalendar(ctx.api,year).then(data=>{if(disposed||ticket!==request)return;source=data;loadedYear=year;draw();}).catch(()=>{if(disposed||ticket!==request)return;source={has_data:false,status:'unavailable',events:[]};loadedYear=year;draw();});
  }
  const resume=()=>{if(!document.hidden)load();};
  draw();queueMicrotask(load);document.addEventListener('visibilitychange',resume);
  return ()=>{disposed=true;request++;document.removeEventListener('visibilitychange',resume);};
}
