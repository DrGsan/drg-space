import {tText,locale} from "../../core/i18n.js";
import {esc,today} from '../../core/ui.js';
import {matchesSource} from '../dashboard/widgets/task-source.js';
export const colors={blue:'#3978dc',sage:'#39876b',amber:'#bd861b',lilac:'#8861c6',rose:'#c45c79',slate:'#69788a'};
export const listIcons={list:tText("tasks:list"),check:tText("tasks:checkbox"),briefcase:tText("tasks:work"),'shopping-cart':tText("tasks:shopping"),home:tText("tasks:home"),star:tText("tasks:star"),calendar:tText("tasks:calendar"),heart:tText("tasks:heart"),book:tText("tasks:book"),gift:tText("tasks:gift")};
export const smartViews=[{id:'today',title:tText("tasks:today"),icon:'calendar',color:'blue'},{id:'scheduled',title:tText("tasks:inPlanning"),icon:'calendar',color:'rose'},{id:'all',title:tText("tasks:all"),icon:'list',color:'slate'},{id:'flagged',title:tText("tasks:checkbox2"),icon:'flag',color:'amber'},{id:'important',title:tText("tasks:important"),icon:'priority',color:'lilac'},{id:'completed',title:tText("tasks:completed"),icon:'check',color:'sage'}];
const shapes={
 grip:'<circle cx="8" cy="5" r="1"/><circle cx="16" cy="5" r="1"/><circle cx="8" cy="12" r="1"/><circle cx="16" cy="12" r="1"/><circle cx="8" cy="19" r="1"/><circle cx="16" cy="19" r="1"/>',
 priority:'<circle cx="12" cy="12" r="9"/><path d="M12 6v7M12 17h.01"/>',
 back:'<path d="m10 5-7 7 7 7m-7-7h18"/>',menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
 list:'<path d="M8 5h13M8 12h13M8 19h13M3 5h.01M3 12h.01M3 19h.01"/>',check:'<path d="m4 12 5 5L20 6"/>',
 calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M7 3v4M17 3v4"/>',flag:'<path d="M5 21V3m0 0c5-4 9 4 14 0v10c-5 4-9-4-14 0"/>',
 plus:'<path d="M12 5v14M5 12h14"/>',close:'<path d="m6 6 12 12M6 18 18 6"/>',info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
 chevron:'<path d="m9 5 7 7-7 7"/>',dots:'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
 users:'<circle cx="9" cy="7" r="4"/><path d="M2 21v-2a7 7 0 0 1 14 0v2m0-18a4 4 0 0 1 0 8m3 5a6 6 0 0 1 3 5"/>',refresh:'<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12-2l2 2M18 17a7 7 0 0 1-12 2l-2-2"/>',
 briefcase:'<rect x="3" y="7" width="18" height="14" rx="2"/><path d="M8 7V4h8v3M3 12h18M9 11v3h6v-3"/>',
 'shopping-cart':'<path d="M2 3h3l3 12h11l3-8H6"/><circle cx="9" cy="20" r="1"/><circle cx="19" cy="20" r="1"/>',home:'<path d="m3 10 9-7 9 7v11H3Zm6 11v-8h6v8"/>',
 star:'<path d="m12 3 2.8 5.7 6.3.9-4.6 4.4 1.1 6.3-5.6-3-5.6 3 1.1-6.3L3 9.6l6.2-.9Z"/>',heart:'<path d="M20 12 12 21 4 12A5 5 0 0 1 12 5a5 5 0 0 1 8 7Z"/>',
 book:'<path d="M3 4h6a3 3 0 0 1 3 3v14a3 3 0 0 0-3-3H3Zm18 0h-6a3 3 0 0 0-3 3v14a3 3 0 0 1 3-3h6Z"/>',gift:'<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M5 12v9h14v-9M12 8v13"/><path d="M12 8H7a3 3 0 1 1 3-3Zm0 0h5a3 3 0 1 0-3-3Z"/>'
};
export const taskIcon=name=>`<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round">${shapes[name]||shapes.list}</svg>`;
export const taskFields=['title','day','time','notes','url','tags','flagged','priority','list_id','parent_id'];
export const editableTask=task=>Object.fromEntries(taskFields.map(field=>[field,field==='tags'?[...(task.tags||[])]:task[field]??(field==='priority'?0:field==='flagged'?false:field==='parent_id'?null:'')]));
export const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
export const patchFields=(draft,base,keys)=>Object.fromEntries(keys.filter(key=>!same(draft[key],base[key])).map(key=>[key,draft[key]]));
export const canManage=(list,me)=>list?.can_manage??list?.owner??list?.owner_id===me.id;
export const listColor=list=>colors[list?.color]||colors.blue;
export function dateLabel(day,time=''){
 if(!day)return '';
 const dayDate=new Date(day+'T12:00:00'),now=today(),tomorrow=new Date();tomorrow.setDate(tomorrow.getDate()+1);
 const tomorrowKey=`${tomorrow.getFullYear()}-${String(tomorrow.getMonth()+1).padStart(2,'0')}-${String(tomorrow.getDate()).padStart(2,'0')}`;
 const label=day===now?tText("tasks:today"):day===tomorrowKey?tText("tasks:tomorrow"):Number.isNaN(dayDate.getTime())?day:dayDate.toLocaleDateString(locale(),{day:'numeric',month:'short',...(dayDate.getFullYear()!==new Date().getFullYear()?{year:'numeric'}:{})});
 return label+(time?' · '+time:'');
}
export function safeTaskUrl(value){
 try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)&&!url.username&&!url.password?url.href:'';}catch{return '';}
}
export function matchesView(task,view,showCompleted=false){
 if(view.scope&&!matchesSource(task,view.scope,today()))return false;
 if(view.kind==='list')return task.list_id===view.id&&(showCompleted||!task.done);
 if(view.kind==='day')return task.day===view.day&&(showCompleted||!task.done);
 if(view.kind==='tag')return (task.tags||[]).includes(view.tag)&&(showCompleted||!task.done);
 if(view.id==='completed')return !!task.done;
 if(task.done&&!showCompleted)return false;
 if(view.id==='today')return !!task.day&&task.day<=today();
 if(view.id==='scheduled')return !!task.day;
 if(view.id==='flagged')return !!task.flagged;
 if(view.id==='important')return Number(task.priority)===3;
 return true;
}
export function titleForView(view,lists){
 if(view.kind==='list')return lists.find(list=>list.id===view.id)?.name||tText("tasks:reminders");
 if(view.kind==='day')return dateLabel(view.day);
 if(view.kind==='tag')return '#'+view.tag;
 return smartViews.find(item=>item.id===view.id)?.title||tText("tasks:all");
}
export function taskMeta(task,list,includeList){
 const link=safeTaskUrl(task.url),notes=task.notes?.trim(),date=dateLabel(task.day,task.time);
 return `<span class="tasks-row-meta">${date?`<time class="${!task.done&&task.day<today()?'is-overdue':''}" datetime="${esc(task.day)}${task.time?'T'+esc(task.time):''}">${esc(date)}</time>`:''}${includeList&&list?`<span>${esc(list.name)}</span>`:''}${(task.tags||[]).map(tag=>`<button type="button" class="tasks-inline-tag" data-task-tag="${esc(tag)}">#${esc(tag)}</button>`).join('')}${link?`<a href="${esc(link)}" target="_blank" rel="noopener noreferrer" title="${esc(task.url)}">${esc(new URL(link).hostname)}</a>`:''}</span>${notes?`<span class="tasks-row-notes">${esc(notes)}</span>`:''}`;
}
