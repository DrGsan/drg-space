import {tText,locale} from "./i18n.js";
import {setPeopleNames,rememberPeople,knownPerson} from './person-names.js';
import {presenceText,isPersonOnline,personName} from './people.js';
// Foreground polling is independent of the heartbeat write: a rejected write
// must not prevent receiving another person's current status.
export function startPresence(getContext,{doc=document,events=window,now=Date.now,every=setInterval,cancel=clearInterval}={}){
  let disposed=false,running=false,lastBeat=null,people=[],peopleScope='contacts';
  function paint(){
    if(disposed)return;
    const ctx=getContext();if((ctx.state.view==='admin'&&['owner','admin'].includes(ctx.state.me?.role))!==(peopleScope==='admin'))return;
    const map=new Map(people.map(p=>[p.id,p]));
    for(const space of getContext().state.spaces||[])for(const person of space.members||[])if(map.has(person.id))Object.assign(person,map.get(person.id));
    doc.querySelectorAll('[data-person-label],[data-person-name]').forEach(el=>{const id=el.dataset.personLabel||el.dataset.personName;el.textContent=personName({id});});
    doc.querySelectorAll('[data-person-avatar]').forEach(el=>{const person=knownPerson({id:el.dataset.personAvatar});if(!person.id||!person.login&&!person.display_name)return;const name=personName(person);el.setAttribute('aria-label',tText("common:avatar", {value0:(name)}));const initial=el.querySelector(':scope>span:first-child');if(initial)initial.textContent=name.slice(0,1).toUpperCase();});
    doc.querySelectorAll('[data-person-presence]').forEach(el=>{const p=map.get(el.dataset.personPresence);if(!p)return;el.textContent=presenceText(p,Date.now()/1000,{precise:el.dataset.presenceFormat==='exact'});el.classList.toggle('is-online',isPersonOnline(p));el.title=p.last_seen?new Date(p.last_seen*1000).toLocaleString(locale()):'';});
    doc.querySelectorAll('[data-person-online]').forEach(el=>{const p=map.get(el.dataset.personOnline);if(p)el.hidden=!isPersonOnline(p);});
  }
  async function refresh(){
    if(disposed)return;paint();if(doc.hidden||running)return;running=true;const ctx=getContext();
    try{
      if(lastBeat===null||now()-lastBeat>=25000){try{await ctx.api('/presence','POST');lastBeat=now();}catch{}}
      const scope=ctx.state.view==='admin'&&['owner','admin'].includes(ctx.state.me?.role)?'admin':'contacts';
      const data=await ctx.api(scope==='admin'?'/presence?scope=admin':'/presence');if(disposed)return;
      // A navigation during the request must not replace full admin history
      // with the limited contact payload (or apply an admin list elsewhere).
      if((getContext().state.view==='admin'&&['owner','admin'].includes(getContext().state.me?.role))!==(scope==='admin'))return;
      people=data.users;peopleScope=scope;rememberPeople(people);
      if(data.people_names){ctx.state.me.people_names=data.people_names;if(setPeopleNames(ctx.state.me.id,data.people_names))doc.dispatchEvent(new Event('drg-people-names-change'));}
      if(data.account_deletion)doc.dispatchEvent(new CustomEvent('drg-account-locked',{detail:data.account_deletion}));
      paint();
      doc.dispatchEvent(new CustomEvent('drg-presence-change',{detail:people}));
    }catch{}finally{running=false;}
  }
  const timer=every(refresh,5000);doc.addEventListener('visibilitychange',refresh);events.addEventListener('focus',refresh);events.addEventListener('online',refresh);refresh();
  return {paint,refresh,destroy(){disposed=true;cancel(timer);doc.removeEventListener('visibilitychange',refresh);events.removeEventListener('focus',refresh);events.removeEventListener('online',refresh);}};
}
