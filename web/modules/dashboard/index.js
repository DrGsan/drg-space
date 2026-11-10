import {tText,tHtml} from "../../core/i18n.js";
import {sidebarIcon} from '../../core/sidebar-icons.js';
import {sectionHeader} from '../../core/section-header.js';
import {displayName} from '../profile/index.js';
import { esc, today, $ } from "../../core/ui.js";
import { widgetRegistry } from "./registry.js";
import { configureWidget } from "./configure.js";
import { openWidgetManager, widgetTitle } from "./manager.js";
import { widgetSize } from './sizes.js';
import { sortableWidgets } from './sortable.js';
import './edit-mode.css';
import {entryTransitionActive,entryTransitionFinished} from '../../core/entry-motion.js';
import themes from '../../../app/seasonal_themes.json';
import {seasonalGreetingTheme,nextSeasonalDateCheck} from '../../core/seasonal-calendar.js';
import {getSeasonalPreview} from '../../core/seasonal.js';
let disposeActive,flushActive;
export const dashboardController={destroy(){disposeActive?.();disposeActive=null;flushActive=null;},flush(){return flushActive?.()??Promise.resolve(true);}};
export function mountDashboard(host,ctx,{initialLayout}={}){
  // Animate the existing Home dialogs without changing their forms or actions.
  const openModal=ctx.modal;
  ctx={...ctx,modal:html=>openModal(html,{motion:true})};
  dashboardController.destroy();let cleanups=new Map(),disposed=false,layout,unlocked=false,pending,dirty=false,sequence=0;
  const disposeWidget=id=>{const cleanup=cleanups.get(id);cleanups.delete(id);cleanup?.();};
  const disposeWidgets=()=>{for(const id of [...cleanups.keys()])disposeWidget(id);};
  ctx.state.day=today();
  host.classList.toggle('dashboard-enter',!entryTransitionActive());
  host.innerHTML=`<div class="page-heading dashboard-heading"><div><p class="eyebrow">${tHtml("dashboard:personalView")}</p><h1>${tHtml("dashboard:hello", {value0:(esc(displayName(ctx.state.me)))})}<span class="hello-dot">.</span></h1><p class="muted" data-day-greeting></p></div><div class="dashboard-tools"><span data-layout-status class="small muted" role="status"></span><button class="icon-btn layout-lock" id="dashboard-lock" aria-label="${tHtml("dashboard:unlockWidgetLayout")}" aria-pressed="false" title="${tHtml("dashboard:editWidgets")}" disabled>${sidebarIcon('lock')}</button><button class="icon-btn" id="customize-dashboard" aria-label="${tHtml("dashboard:customiseHome")}" title="${tHtml("dashboard:customiseHome")}">${sidebarIcon('settings')}</button></div></div><p class="hint" data-layout-help hidden>${tHtml("dashboard:moveWidgetsUsingTheHandleOrTheArrows")}</p><div class="widget-grid" id="widget-grid"><span class="muted" role="status">${tHtml("dashboard:loadingWidgets")}</span></div><p class="quiet-note">${tHtml("dashboard:yourPersonalPagesAreVisibleToYouShared")}</p>`;
  const updateGreeting=()=>{const theme=seasonalGreetingTheme(themes,new Date(),getSeasonalPreview());$('[data-day-greeting]',host).textContent=tText('dashboard:'+(theme?.phrase_key||'aLittleClarityForTheNewDay'));};
  updateGreeting();window.addEventListener('drg-seasonal-preview',updateGreeting);
  sectionHeader(host,ctx);
  const status=value=>{if(!disposed)$('[data-layout-status]',host).textContent=value;};
  const applyOrder=ids=>{
    const visible=new Set(ids),byId=new Map(layout.instances.map(item=>[item.id,item]));let i=0;
    layout.instances=layout.instances.map(item=>visible.has(item.id)?byId.get(ids[i++]):item);
    dirty=true;sequence++;status(tText("dashboard:saving"));flushActive();
  };
  const gridHost=$('#widget-grid',host);
  let geometryFrame=null,geometryWidth;
  const syncGridGeometry=(width=gridHost.getBoundingClientRect().width)=>{
    const columns=width>=840?4:width>=440?2:1,cell=`${(width-(columns-1)*16)/columns}px`;
    if(gridHost.style.getPropertyValue('--grid-columns')!==String(columns))gridHost.style.setProperty('--grid-columns',columns);
    if(gridHost.style.getPropertyValue('--grid-cell')!==cell)gridHost.style.setProperty('--grid-cell',cell);
  };
  // Apply geometry outside ResizeObserver delivery: changing rows also resizes the observed grid.
  const resize=new ResizeObserver(entries=>{
    geometryWidth=entries[0].contentRect.width;
    if(geometryFrame===null)geometryFrame=requestAnimationFrame(()=>{
      geometryFrame=null;if(!disposed&&gridHost.isConnected)syncGridGeometry(geometryWidth);
    });
  });
  syncGridGeometry();
  resize.observe(gridHost);
  const sorter=sortableWidgets(gridHost,applyOrder);
  const flush=async()=>{
    sorter.finish();
    if(pending){const ok=await pending;return ok&&dirty?flush():ok;}
    if(!dirty)return true;
    const seq=sequence,draft=structuredClone(layout);
    pending=(async()=>{try{
      const saved=await ctx.api('/dashboard/layout','PUT',draft);
      layout.version=saved.version;dirty=sequence!==seq;ctx.state.me.settings.dashboard=structuredClone(layout);
      status(dirty?tText("dashboard:saving"):tText("dashboard:saved"));return true;
    }catch(error){status(tText("dashboard:notSavedTapTheLockToRepeat"));ctx.toast(error.message);return false;}finally{pending=null;}})();
    const ok=await pending;return ok&&dirty?flush():ok;
  };
  flushActive=flush;
  const unload=e=>{if(dirty||pending){e.preventDefault();e.returnValue='';}};
  window.addEventListener('beforeunload',unload);
  disposeActive=()=>{disposed=true;host.classList.remove('dashboard-enter');sorter.destroy();resize.disconnect();if(geometryFrame!==null)cancelAnimationFrame(geometryFrame);disposeWidgets();window.removeEventListener('beforeunload',unload);clearTimeout(dayTimer);document.removeEventListener('visibilitychange',checkDay);window.removeEventListener('focus',checkDay);window.removeEventListener('drg-seasonal-preview',updateGreeting);};
  $('#dashboard-lock',host).onclick=async()=>{
    if(!(await flush()))return;
    unlocked=!unlocked;sorter.lock(!unlocked);
    const button=$('#dashboard-lock',host);button.innerHTML=sidebarIcon(unlocked?'unlock':'lock');button.setAttribute('aria-pressed',String(unlocked));button.setAttribute('aria-label',unlocked?tText("dashboard:lockWidgetLayout"):tText("dashboard:unlockWidgetLayout"));$('[data-layout-help]',host).hidden=!unlocked;
  };
  const removeWidget=instance=>{
    if(!unlocked||disposed)return;
    const dialog=ctx.modal(`<h2>${tHtml("dashboard:areYouSureYouWantToDeleteThis")}</h2><p class="muted">${tHtml("dashboard:willBeRemovedFromTheMainScreenData", {value0:(esc(widgetTitle(instance)))})}</p><div class="widget-delete-actions"><button class="secondary" data-cancel-widget-delete>${tHtml("dashboard:no")}</button><button class="primary" data-confirm-widget-delete>${tHtml("dashboard:yes")}</button></div>`);
    const yes=$('[data-confirm-widget-delete]',dialog),no=$('[data-cancel-widget-delete]',dialog);
    no.onclick=()=>$('.modal-close',dialog).click();
    yes.onclick=async()=>{
      yes.disabled=true;no.disabled=true;let committing=false,deleted=false;
      try{
        if(!(await flush())||disposed||!dialog.isConnected)return;
        const draft=structuredClone(layout),index=draft.instances.findIndex(item=>item.id===instance.id);
        if(index<0)throw new Error(tText("dashboard:widgetAlreadyDeletedReopenTheMainScreen"));
        draft.instances.splice(index,1);
        committing=true;sorter.lock(true);$('#dashboard-lock',host).disabled=true;
        pending=(async()=>{
          try{
            const saved=await ctx.api('/dashboard/layout','PUT',draft);
            if(!disposed){
              layout=saved;ctx.state.me.settings.dashboard=structuredClone(saved);
              const removed=gridHost.querySelector(`[data-widget-id="${instance.id}"]`);
              removed?.remove();try{disposeWidget(instance.id);}catch(error){ctx.toast(error.message);}
              if(!gridHost.querySelector(':scope > [data-widget-id]'))gridHost.innerHTML=`<div class="empty"><h3>${tHtml("dashboard:theMainScreenIsCurrentlyEmpty")}</h3><p>${tHtml("dashboard:addWidgetsByTappingCustomizeMainScreen")}</p></div>`;
              syncGridGeometry();status(tText("dashboard:widgetDeleted"));deleted=true;$('.modal-close',dialog).click();
            }
            return true;
          }catch(error){status(tText("dashboard:failedToDeleteWidget"));ctx.toast(error.message);return false;}
          finally{pending=null;}
        })();
        await pending;
      }catch(error){ctx.toast(error.message);}
      finally{
        yes.disabled=false;no.disabled=false;
        if(committing&&!disposed){sorter.lock(!unlocked);$('#dashboard-lock',host).disabled=false;if(deleted)$('#dashboard-lock',host).focus();}
      }
    };
  };
  const renderWidgets=()=>{
    if(disposed||!host.isConnected||!layout)return;
    disposeWidgets();const grid=$('#widget-grid',host);grid.innerHTML='';
    for(const instance of layout.instances){
      const spec=widgetRegistry.get(instance.module_id);
      if(!spec||instance.hidden||!ctx.moduleEnabled(instance.module_id))continue;
      const section=document.createElement('section'),title=widgetTitle(instance);
      const size=widgetSize(instance.size),[cols,rows]=size.split('x').map(Number);
      section.className=`panel widget widget-${size} ${spec.className || ''}`;
      section.style.setProperty('--entry-delay',`${Math.min(grid.childElementCount,5)*45}ms`);
      section.style.setProperty('--widget-cols',cols);section.style.setProperty('--widget-rows',rows);
      section.dataset.widget=instance.module_id;section.dataset.widgetId=instance.id;section.setAttribute('aria-label',title);
      section.innerHTML=`<header class="widget-toolbar"><button class="icon-btn widget-grip" data-move-widget hidden aria-label="${tHtml("dashboard:moveWidget", {value0:(esc(title))})}" title="${tHtml("dashboard:dragOrUseTheArrows")}">⋮⋮</button><h2>${esc(title)}</h2><button class="icon-btn" data-configure hidden aria-label="${tHtml("dashboard:configureWidget", {value0:(esc(title))})}">⚙</button><button class="icon-btn widget-remove" data-remove-widget hidden aria-label="${tHtml("dashboard:deleteWidget", {value0:(esc(title))})}">×</button></header><div class="widget-content-frame"><div data-widget-body inert aria-hidden="true"></div><div class="widget-content-placeholder" role="status"><span class="sr-only">${tHtml("dashboard:loadingWidgets")}</span><i aria-hidden="true"></i><i aria-hidden="true"></i><i aria-hidden="true"></i></div></div>`;
      grid.append(section);
      const configure=async()=>{if(!(await flush()))return;configureWidget(instance,ctx,async(next)=>{
        const draft=structuredClone(layout),index=draft.instances.findIndex(item=>item.id===next.id);
        if(index<0)throw new Error(tText("dashboard:widgetAlreadyDeletedReopenTheSettings"));
        draft.instances[index]=next;layout=await ctx.api('/dashboard/layout','PUT',draft);ctx.state.me.settings.dashboard=layout;renderWidgets();
      });};
      $('[data-configure]',section).onclick=configure;
      $('[data-remove-widget]',section).onclick=()=>removeWidget(instance);
      const firstRequests=[];let mounting=true;
      const widgetAPI=(...args)=>{const result=ctx.api(...args);if(mounting)firstRequests.push(result);return result;};
      try{
        const result=spec.mount($('[data-widget-body]',section),{...ctx,api:widgetAPI,instance,configure,refresh:renderWidgets});
        if(result?.then)firstRequests.push(result);
        const keep=(resource)=>{const fn=typeof resource==='function'?resource:resource?.dispose?.bind(resource);if(fn){if(disposed||!section.isConnected)fn();else cleanups.set(instance.id,fn);}};
        if(result?.then)result.then(keep).catch(error=>{if(section.isConnected)$('[data-widget-body]',section).textContent=error.message;});else keep(result);
      }catch(error){$('[data-widget-body]',section).textContent=error.message;}
      mounting=false;
      Promise.allSettled([...firstRequests,entryTransitionFinished()]).then(()=>{
        if(disposed||!section.isConnected)return;
        requestAnimationFrame(()=>{
          if(disposed||!section.isConnected)return;
          const body=$('[data-widget-body]',section);body.inert=false;body.removeAttribute('aria-hidden');
          section.classList.add('widget-ready');
          section.addEventListener('transitionend',event=>{if(event.target===body)$('.widget-content-placeholder',section)?.remove();});
          if(matchMedia('(prefers-reduced-motion: reduce)').matches)$('.widget-content-placeholder',section)?.remove();
        });
      });
    }
    if(!grid.childElementCount)grid.innerHTML=`<div class="empty"><h3>${tHtml("dashboard:theMainScreenIsCurrentlyEmpty")}</h3><p>${tHtml("dashboard:addWidgetsByTappingCustomizeMainScreen")}</p></div>`;
    sorter.refresh();
  };
  let currentDay=today();
  const checkDay=async()=>{
    clearTimeout(dayTimer);if(disposed)return;dayTimer=setTimeout(checkDay,nextSeasonalDateCheck());if(document.hidden||currentDay===today())return;updateGreeting();
    currentDay=today();ctx.state.day=currentDay;ctx.state.month=new Date();
    try{if(ctx.moduleEnabled('tasks'))ctx.state.tasks=await ctx.api('/tasks');if(!disposed)renderWidgets();}catch(error){ctx.toast(error.message);}
  };
  let dayTimer=setTimeout(checkDay,nextSeasonalDateCheck());document.addEventListener('visibilitychange',checkDay);window.addEventListener('focus',checkDay);
  $('#customize-dashboard',host).onclick=async()=>{if(await flush())openWidgetManager(ctx,saved=>{layout=saved;renderWidgets();});};
  const seed=initialLayout??ctx.state.me.settings?.dashboard;
  if(seed&&Array.isArray(seed.instances)&&Number.isInteger(seed.version)){
    layout=seed;renderWidgets();$('#dashboard-lock',host).disabled=false;
  }
  if(!initialLayout)ctx.api('/dashboard/layout').then(data=>{if(!disposed&&host.isConnected){
    const changed=JSON.stringify(layout)!==JSON.stringify(data);
    layout=data;ctx.state.me.settings.dashboard=data;if(changed)renderWidgets();$('#dashboard-lock',host).disabled=false;
  }}).catch(error=>{if(!disposed&&host.isConnected){if(!layout)gridHost.textContent=error.message;else status(error.message);}});
}
