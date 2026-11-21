import {esc} from './ui.js';
import {sidebarIcon} from './sidebar-icons.js';
import {readSession,writeSession} from './navigation.js';
import './preferences.css';

// Move existing panels into native disclosures without remounting their forms.
// Open groups are per account and section; no preferences are sent to the API.
export function preferenceSections(container,sections,{userId,scope}={}){
  const key=`drg-preference-sections:${userId}:${scope}`;
  let saved=[];try{saved=JSON.parse(readSession(key)||'[]');}catch{}
  const open=new Set(Array.isArray(saved)?saved.filter(id=>sections.some(section=>section.id===id)):[]);
  let group,rows,lastGroup;
  for(const section of sections){
    const panel=container.querySelector(section.selector);if(!panel)continue;
    if(section.group!==lastGroup){
      group=document.createElement('section');group.className='preference-group';
      const heading=document.createElement('h2');heading.className='preference-group-title';heading.textContent=section.group;
      rows=document.createElement('div');rows.className='preference-rows';group.append(heading,rows);container.append(group);lastGroup=section.group;
    }
    const details=document.createElement('details');details.className='preference-row'+(scope==='settings'?' mobile-setting-row':'');details.dataset.preferenceKey=section.id;
    const summary=document.createElement('summary');
    summary.innerHTML=`<span class="preference-icon">${sidebarIcon(section.icon)}</span><span class="preference-label"><strong>${esc(section.title)}</strong><span class="preference-description">${esc(section.description)}</span></span><span class="preference-status" data-preference-status hidden></span>`;
    panel.classList.add('preference-body');rows.append(details);details.append(summary,panel);details.open=open.has(section.id);
    details.addEventListener('toggle',()=>{if(!details.isConnected)return;if(details.open)open.add(section.id);else open.delete(section.id);writeSession(key,JSON.stringify([...open]));});
  }
  container.closest('#view-content')?.classList.add('preferences-surface');
}
export function preferenceStatus(panel,message){
  const status=panel.closest('.preference-row')?.querySelector('[data-preference-status]');
  if(status){status.textContent=message;status.hidden=!message;}
}
