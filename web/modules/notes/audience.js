import {tText,tHtml} from "../../core/i18n.js";
import {esc} from '../../core/ui.js';
import {personName} from '../../core/people.js';
const roles=[['',tText("notes:notSelected")],['viewer',tText("notes:view")],['commenter',tText("notes:commenting")],['editor',tText("notes:editing")]];
const options=selected=>roles.map(([id,label])=>`<option value="${id}" ${selected===id?'selected':''}>${label}</option>`).join('');

export function audienceMarkup(value){
  if(!value)return '';
  return `<details class="note-audience-settings"><summary>${tHtml("notes:commandsAndBranchAccess")}</summary><form data-note-audience><label>${tHtml("notes:branchAccess")}<select name="mode" aria-label="${tHtml("notes:branchAccess")}">${[['inherit',tText("notes:inheritFromParent")],['workspace',tText("notes:allWorkspaceParticipants")],['restricted',tText("notes:selectedTeamsAndPeople")]].map(([id,label])=>`<option value="${id}" ${value.mode===id?'selected':''}>${label}</option>`).join('')}</select></label><div data-audience-selection ${value.mode==='restricted'?'':'hidden'}><details open><summary>${tHtml("notes:teams")}</summary><div class="note-audience-list">${value.teams_catalog.map(t=>`<label><span>${esc(t.name)}<small>${tHtml("notes:participants", {value0:(t.count)})}</small></span><select data-audience-team="${esc(t.id)}" aria-label="${tHtml("notes:teamAccess", {value0:(esc(t.name))})}">${options(value.teams[t.id]||'')}</select></label>`).join('')||`<p class="hint">${tHtml("notes:createTeamsInWorkspaceManagement")}</p>`}</div></details><details><summary>${tHtml("notes:individualParticipants")}</summary><div class="note-audience-list">${value.people_catalog.map(u=>`<label><span>${esc(personName(u))}<small>@${esc(u.login)}</small></span>${value.manager_ids.includes(u.id)?`<small>${tHtml("notes:fullAccess")}</small>`:`<select data-audience-person="${esc(u.id)}" aria-label="${tHtml("notes:personalAccess", {value0:(esc(u.login))})}">${options(value.members[u.id]||'')}</select>`}</label>`).join('')}</div></details></div><p class="hint">${tHtml("notes:permissionsForTeamsAndPeopleAreCombinedThe")}</p><button type="submit" class="secondary compact">${tHtml("notes:saveBranchAccess")}</button></form></details>`;
}

export function bindAudience(host,value,save,preview){
  const form=host.querySelector('[data-note-audience]');if(!form)return;
  form.elements.mode.onchange=()=>{form.querySelector('[data-audience-selection]').hidden=form.elements.mode.value!=='restricted';};
  form.onsubmit=async e=>{
    e.preventDefault();const mode=form.elements.mode.value,teams={},members={};
    if(mode==='restricted'){
      form.querySelectorAll('[data-audience-team]').forEach(el=>{if(el.value)teams[el.dataset.audienceTeam]=el.value;});
      form.querySelectorAll('[data-audience-person]').forEach(el=>{if(el.value)members[el.dataset.audiencePerson]=el.value;});
    }
    const next={revision:value.revision,mode,teams,members};
    if(!preview){save(next);return;}
    const submit=form.querySelector('[type=submit]');submit.disabled=true;
    form.querySelector('[data-audience-preview]')?.remove();
    try{
      const result=await preview(next),labels={editor:tText("notes:editing"),commenter:tText("notes:commenting"),viewer:tText("notes:view")};
      const box=document.createElement('div');box.dataset.audiencePreview='';box.className='note-audience-preview';
      box.innerHTML=`<strong>${tHtml("notes:accessChanges")}</strong><p class="hint">${tHtml("notes:pagesCheckedIndividualSubpageSettingsHaveBeenTaken", {value0:(result.pages)})}</p>${result.changes.map(u=>`<p>${esc(personName(u))}: ${esc(labels[u.before]||tText("notes:noAccess"))} → ${esc(labels[u.after]||tText("notes:noAccess"))}<small>${tHtml("notes:pagesAffected", {value0:(u.pages)})}</small></p>`).join('')||`<p>${tHtml("notes:participantAccessLevelsWillNotChange")}</p>`}<button type="button" class="primary compact">${tHtml("notes:applyChanges")}</button>`;
      form.append(box);box.querySelector('button').onclick=()=>save(next);
      form.addEventListener('change',()=>box.remove(),{once:true});
    }catch(error){const message=document.createElement('p');message.className='error';message.textContent=error.message;message.dataset.audiencePreview='';form.append(message);}
    finally{submit.disabled=false;}

  };
}

export function accessExplanation(user){
  return (user.sources||[]).map(s=>s.kind==='team'?tText("notes:team", {value0:(s.label), value1:(roles.find(r=>r[0]===s.role)?.[1]||s.role)}):s.label).join('; ');
}
