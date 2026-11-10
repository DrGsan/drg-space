import {tText,tHtml,locale} from "../../core/i18n.js";
import {labelTableRows} from '../../core/catalog.js';
import {$,esc} from '../../core/ui.js';
import {personAvatar,personName,personPresence,personSearch} from '../../core/people.js';

export const spaceRoles={owner:tText("social:owner"),admin:tText("social:administrator"),member:tText("social:member")};
const compare=(a,b)=>a.localeCompare(b,locale(),{numeric:true,sensitivity:'base'});
const day=value=>value?new Date(value*1000).toLocaleDateString(locale()):'—';
export function avatarStack(people){
  return `<span class="space-avatar-stack">${people.slice(0,5).map(personAvatar).join('')}${people.length>5?`<span class="space-avatar-more">+${people.length-5}</span>`:''}</span>`;
}

export function peopleTable(host,state,{onRole,onRemove,onName,selection,view={}}={}){
  const teams=state.teams||{},members=state.members||[],editable=!!onRole,selecting=!!selection,compact=selecting&&selection.compact!==false;
  Object.assign(view,{query:view.query||'',role:view.role||'',team:view.team||'',page:view.page||0,reverse:view.reverse||false});
  const showTeams=selecting||state.advanced_available||Object.keys(teams).length>0;
  const columns=(compact?2:4)+(selecting?1:0)+(showTeams?1:0)+(onRemove?1:0);
  host.innerHTML=`<div class="space-table-toolbar"><input type="search" data-people-search aria-label="${tHtml("social:findParticipants")}" placeholder="${tHtml("social:searchByNameOrLogin")}" value="${esc(view.query)}"><select data-people-role aria-label="${tHtml("social:filterByRole")}"><option value="">${tHtml("social:allRoles")}</option>${Object.entries(spaceRoles).map(([id,name])=>`<option value="${id}" ${view.role===id?'selected':''}>${name}</option>`).join('')}</select>${Object.keys(teams).length?`<select data-people-team aria-label="${tHtml("social:filterByTeam")}"><option value="">${tHtml("social:allTeams2")}</option>${Object.entries(teams).map(([id,t])=>`<option value="${esc(id)}" ${view.team===id?'selected':''}>${esc(t.name)}</option>`).join('')}</select>`:''}</div><div class="space-table-scroll" tabindex="0" role="region" aria-label="${tHtml("social:participantTable")}"><table class="space-table"><thead><tr>${selecting?`<th scope="col"><input type="checkbox" data-select-page aria-label="${tHtml('social:selectAllOnPage')}"></th>`:''}<th scope="col" data-people-sort-heading><button type="button" data-people-sort>${tHtml('social:memberLabel')} <span data-sort-arrow>↑</span></button></th>${showTeams?`<th scope="col">${tHtml('social:teams2')}</th>`:''}${compact?'':`<th scope="col">${tHtml('social:activity')}</th><th scope="col">${tHtml('social:joined')}</th>`}<th scope="col">${tHtml('social:roleLabel')}</th>${onRemove?`<th scope="col">${tHtml('social:actions')}</th>`:''}</tr></thead><tbody></tbody></table></div><div class="space-table-pagination"><span data-people-count aria-live="polite"></span><div><button type="button" class="secondary compact" data-people-prev aria-label="${tHtml("social:previousPageOfParticipants")}">←</button><button type="button" class="secondary compact" data-people-next aria-label="${tHtml("social:nextPageOfParticipants")}">→</button></div></div>`;
  const body=$('tbody',host);
  function render(){
    const query=view.query.trim().toLocaleLowerCase(locale());
    const filtered=members.filter(u=>(!query||personSearch(u).includes(query))&&(!view.role||u.space_role===view.role)&&(!view.team||teams[view.team]?.members.includes(u.id))).sort((a,b)=>(view.reverse?-1:1)*compare(personName(a),personName(b)));
    view.page=Math.min(view.page,Math.max(0,Math.ceil(filtered.length/10)-1));
    const start=view.page*10;
    body.innerHTML=filtered.slice(start,start+10).map(u=>{
      const groups=Object.values(teams).filter(t=>t.members.includes(u.id));
      const role=editable&&u.space_role!=='owner'&&(state.advanced_available||u.space_role==='admin')?`<select aria-label="${tHtml("social:role2", {value0:(esc(u.login))})}" data-space-member-role="${esc(u.id)}"><option value="member" ${u.space_role==='member'?'selected':''}>${tHtml("social:member")}</option><option value="admin" ${u.space_role==='admin'?'selected':''}>${tHtml("social:administrator")}</option></select>`:`<span class="space-role-badge">${spaceRoles[u.space_role]||tText("social:member")}</span>`;
      return `<tr data-people-row="${esc(u.id)}">${selecting?`<td><input type="checkbox" name="member" aria-label="${tHtml("social:select", {value0:(esc(u.login))})}" value="${esc(u.id)}" ${selection.ids.has(u.id)?'checked':''} ${selection.disabled?'disabled':''}></td>`:''}<td><span class="space-table-person">${personAvatar(u)}<span><strong>${onName&&u.id!==state.me_id?`<button type="button" class="text-link person-name-button" data-person-name="${esc(u.id)}" aria-label="${tHtml("social:personalNameFor", {value0:(esc(u.login))})}" title="${tHtml("social:changeYourOwnName")}">${esc(personName(u))}</button>`:esc(personName(u))}</strong><small>@${esc(u.login)}</small></span></span></td>${showTeams?`<td><span class="space-team-badges">${groups.map(t=>`<span>${esc(t.name)}</span>`).join('')||'<span class="space-table-empty">—</span>'}</span></td>`:''}${compact?'':`<td>${personPresence(u)}</td><td class="space-table-date">${day(u.joined_at)}</td>`}<td>${compact?`<span class="space-role-badge">${spaceRoles[u.space_role]||tText("social:member")}</span>`:role}</td>${onRemove?`<td>${u.id!==state.me_id&&u.space_role!=='owner'&&(state.role==='owner'||u.space_role==='member')?`<button type="button" class="text-link danger-link" data-table-remove="${esc(u.id)}" aria-label="${tHtml("social:removeParticipant3", {value0:(esc(u.login))})}">${tHtml("social:delete2")}</button>`:''}</td>`:''}</tr>`;
    }).join('')||`<tr><td colspan="${columns}" class="space-table-empty">${tHtml("social:noParticipantsFound")}</td></tr>`;
    if(selecting){const visible=filtered.slice(start,start+10),all=$('[data-select-page]',host);all.disabled=!!selection.disabled||!visible.length;all.checked=!!visible.length&&visible.every(u=>selection.ids.has(u.id));all.indeterminate=!all.checked&&visible.some(u=>selection.ids.has(u.id));all.onchange=()=>{for(const u of visible){if(all.checked)selection.ids.add(u.id);else selection.ids.delete(u.id);}selection.changed?.();render();};}
    labelTableRows($('table',host));
    $('[data-people-count]',host).textContent=filtered.length?tText("social:of", {value0:(start+1), value1:(Math.min(start+10,filtered.length)), value2:(filtered.length)}):tText("social:label0Participants");
    $('[data-people-prev]',host).disabled=view.page===0;
    $('[data-people-next]',host).disabled=start+10>=filtered.length;
    $('[data-sort-arrow]',host).textContent=view.reverse?'↓':'↑';
    $('[data-people-sort-heading]',host).setAttribute('aria-sort',view.reverse?'descending':'ascending');
    body.querySelectorAll('[data-person-name]').forEach(el=>el.onclick=()=>onName(members.find(u=>u.id===el.dataset.personName)));
    body.querySelectorAll('[data-space-member-role]').forEach(el=>el.onchange=()=>onRole(el.dataset.spaceMemberRole,el.value));
    body.querySelectorAll('[data-table-remove]').forEach(el=>el.onclick=()=>onRemove(el.dataset.tableRemove));
    if(selecting)body.querySelectorAll('[name=member]').forEach(el=>el.onchange=()=>{if(el.checked)selection.ids.add(el.value);else selection.ids.delete(el.value);selection.changed?.();const all=$('[data-select-page]',host),boxes=[...body.querySelectorAll('[name=member]')];all.checked=boxes.every(b=>b.checked);all.indeterminate=!all.checked&&boxes.some(b=>b.checked);});
  }
  $('[data-people-search]',host).onkeydown=e=>{if(e.key==='Enter')e.preventDefault();};
  $('[data-people-search]',host).oninput=e=>{view.query=e.target.value;view.page=0;render();};
  $('[data-people-role]',host).onchange=e=>{view.role=e.target.value;view.page=0;render();};
  const team=$('[data-people-team]',host);if(team)team.onchange=e=>{view.team=e.target.value;view.page=0;render();};
  $('[data-people-sort]',host).onclick=()=>{view.reverse=!view.reverse;view.page=0;render();};
  $('[data-people-prev]',host).onclick=()=>{view.page--;render();};
  $('[data-people-next]',host).onclick=()=>{view.page++;render();};
  render();
}

export function teamsTable(host,state,open,view={}){
  view.query||='';view.page||=0;
  host.innerHTML=`<div class="space-table-toolbar"><input type="search" data-team-search aria-label="${tHtml("social:findTeams")}" placeholder="${tHtml("social:findTeams")}" value="${esc(view.query)}"></div><div class="space-table-scroll" tabindex="0" role="region" aria-label="${tHtml("social:teamTable")}"><table class="space-table"><thead><tr><th scope="col">${tHtml("social:team")}</th><th scope="col">${tHtml("social:members")}</th><th scope="col">${tHtml("social:total")}</th><th scope="col">${tHtml("social:actions")}</th></tr></thead><tbody></tbody></table></div><div class="space-table-pagination"><span data-team-count aria-live="polite"></span><div><button type="button" class="secondary compact" data-team-prev aria-label="${tHtml("social:previousPageOfTeams")}">←</button><button type="button" class="secondary compact" data-team-next aria-label="${tHtml("social:nextPageOfTeams")}">→</button></div></div>`;
  function render(){
    const entries=Object.entries(state.teams).filter(([,t])=>t.name.toLocaleLowerCase(locale()).includes(view.query.trim().toLocaleLowerCase(locale()))).sort(([,a],[,b])=>compare(a.name,b.name));
    view.page=Math.min(view.page,Math.max(0,Math.ceil(entries.length/10)-1));const start=view.page*10;
    $('tbody',host).innerHTML=entries.slice(start,start+10).map(([id,t])=>`<tr data-team-row="${esc(id)}"><td><button type="button" class="space-team-open" data-open-team="${esc(id)}">${esc(t.name)}</button></td><td>${avatarStack(state.members.filter(u=>t.members.includes(u.id)))}</td><td>${t.members.length}</td><td><button type="button" class="text-link" data-open-team="${esc(id)}">${tHtml("social:open")}</button></td></tr>`).join('')||`<tr><td colspan="4" class="space-table-empty">${tHtml("social:noTeamsFound")}</td></tr>`;
    labelTableRows($('table',host));
    host.querySelectorAll('[data-open-team]').forEach(b=>b.onclick=()=>open(b.dataset.openTeam));
    $('[data-team-count]',host).textContent=entries.length?tText("social:of", {value0:(start+1), value1:(Math.min(start+10,entries.length)), value2:(entries.length)}):tText("social:label0Teams");
    $('[data-team-prev]',host).disabled=view.page===0;$('[data-team-next]',host).disabled=start+10>=entries.length;
  }
  $('[data-team-search]',host).oninput=e=>{view.query=e.target.value;view.page=0;render();};
  $('[data-team-prev]',host).onclick=()=>{view.page--;render();};$('[data-team-next]',host).onclick=()=>{view.page++;render();};render();
}
