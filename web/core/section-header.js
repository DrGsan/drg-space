import {tText} from "./i18n.js";
import {esc} from './ui.js';
import {mobileApp} from './mobile.js';
import './section-header.css';

const headers=new WeakMap();
const titles={home:tText("common:home"),settings:tText("common:settings"),admin:tText("common:administration"),social:tText("common:peopleAndGroups"),expenses:tText("common:splitBills"),files:tText("common:files"),family_photos:tText("common:familyPhotos"),security:tText("common:security"),'personal-data':tText("common:exportAndImport")};

// Keep the actual bell (and its handlers) through asynchronous section reloads.
// This helper never applies to the independent notes, tasks or chat workspaces.
export function sectionHeader(host,ctx,{mobileActions=[]}={}){
  const workspace=host.closest('.workspace'),view=workspace?.dataset.currentView;
  if(!workspace||!titles[view])return;
  const bar=workspace.querySelector('.content>.topbar');if(!bar)return;
  let saved=headers.get(workspace);
  if(!saved){saved={bell:workspace.querySelector('#open-inbox')};headers.set(workspace,saved);}
  let heading=host.querySelector(':scope>.page-heading');
  if(!heading){heading=document.createElement('div');heading.className='page-heading';heading.innerHTML=`<h1>${esc(titles[view])}</h1>`;host.prepend(heading);}
  heading.classList.add('section-heading','preferences-heading');
  heading.querySelectorAll('.eyebrow').forEach(el=>el.remove());
  heading.querySelectorAll('.hello-dot').forEach(el=>el.replaceWith(document.createTextNode(view==='home'?el.textContent:'')));
  let copy=heading.querySelector(':scope>.section-heading-copy');
  if(!copy){
    copy=document.createElement('div');copy.className='section-heading-copy';
    const title=heading.querySelector('h1');
    if(title&&title.parentElement!==heading){const group=title.parentElement;copy.append(...group.childNodes);group.remove();}
    else if(title)copy.append(title);
    heading.querySelectorAll(':scope>p').forEach(p=>copy.append(p));heading.prepend(copy);
  }
  let actions=heading.querySelector(':scope>.section-heading-actions');
  if(!actions){
    actions=document.createElement('div');actions.className='section-heading-actions';
    [...heading.children].filter(el=>el!==copy&&!el.matches('.inbox-button')).forEach(el=>actions.append(el));
    heading.append(actions);
  }
  actions.querySelectorAll('button:not(.icon-btn)').forEach(button=>{
    if(!button.hasAttribute('aria-label'))button.setAttribute('aria-label',button.textContent.trim());
    if(!button.title)button.title=button.getAttribute('aria-label');
    button.dataset.sectionIcon=button.matches('[data-back],[data-back-settings]')?'‹':button.matches('[data-start-night]')?'＋':button.matches('#customize-dashboard')?'⚙':'…';
  });
  if(mobileApp()){
    bar.hidden=false;if(saved.bell)bar.append(saved.bell);
    for(const selector of mobileActions){
      const button=heading.querySelector(selector);if(!button)continue;
      bar.querySelectorAll(selector).forEach(old=>{if(old!==button)old.remove();});
      bar.insertBefore(button,saved.bell||null);
    }
  }else{
    bar.hidden=true;
    if(saved.bell)heading.append(saved.bell);
  }
  const visibleActions=[...actions.children].some(el=>!el.matches('[data-back],[data-back-settings]'));
  heading.classList.toggle('has-section-actions',visibleActions);
}
