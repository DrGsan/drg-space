import {tText,tHtml} from "../../core/i18n.js";
import { $, esc } from "../../core/ui.js";
import { widgetRegistry } from "./registry.js";
import { configureWidget } from "./configure.js";
import {clockDefaults,clockRegions} from './widgets/clock-format.js';

export const widgetTitle = (item) => item.title || widgetRegistry.get(item.module_id)?.title || item.module_id;
export async function openWidgetManager(ctx, changed=()=>{}) {
  const el=ctx.modal(`<h2>${tHtml("dashboard:yourMainScreen")}</h2><p class="muted small">${tHtml("dashboard:addWidgetsImmediatelyIncludingMultipleOnesWithDifferent")}</p><div data-manager>${tHtml("dashboard:loading")}</div>`);
  try {
    const draft=structuredClone(await ctx.api('/dashboard/layout'));
    if(!el.isConnected)return;
    const host=$('[data-manager]',el);
    host.innerHTML=`<div id="layout-modules"></div><div class="add-widget-row"><label>${tHtml("dashboard:newWidget")}<select data-widget-type></select></label><button class="secondary" data-add-widget>${tHtml("dashboard:addWidget")}</button></div><button class="primary" data-save-layout>${tHtml("dashboard:saveSettings")}</button>`;
    const catalog=ctx.state.moduleCatalog.map(module=>widgetRegistry.get(module.id)).filter(Boolean);
    $('[data-widget-type]',host).innerHTML=catalog.map(spec=>`<option value="${spec.id}" ${ctx.moduleEnabled(spec.moduleId)?'':'disabled'}>${esc(spec.title)}${ctx.moduleEnabled(spec.moduleId)?'':(" "+tText("dashboard:sectionDisabled")+"")}</option>`).join('');
    const firstAvailable=$('[data-widget-type] option:not(:disabled)',host);
    if(firstAvailable)$('[data-widget-type]',host).value=firstAvailable.value;
    const list=()=>{
      $('#layout-modules',host).innerHTML=draft.instances.map((item,i)=>{
        const title=widgetTitle(item),enabled=ctx.moduleEnabled(item.module_id);
        const zone=item.config.timezone||clockDefaults.timezone;
        const description=item.module_id==='weather'?(item.config.city || tText("dashboard:cityNotSelected")):item.module_id==='clock'?`${item.config.label || (item.config.mode==='digital'?tText("dashboard:digital"):tText("dashboard:analog"))} · ${clockRegions.find(([value])=>value===zone)?.[1]||zone}`:item.module_id==='rss'?tText("dashboard:sources", {value0:(item.config.feeds?.length || 0)}):item.module_id==='bookmarks'?tText("dashboard:shortcuts", {value0:(item.config.links?.length || 0)}):item.module_id==='media'?tText("dashboard:images", {value0:(item.config.items?.length || 0)}):'';
        return `<div class="instance-row"><label class="check-label"><input type="checkbox" data-visible="${i}" ${enabled?'':'disabled'} ${enabled&&!item.hidden?'checked':''}>${esc(title)}</label><span class="small muted">${enabled?esc(description):tText("dashboard:sectionDisabled2")}</span><div class="instance-actions"><button class="text-link" data-configure="${i}" aria-label="${tHtml("dashboard:settings", {value0:(esc(title))})}" ${enabled?'':'disabled'}>${tHtml("dashboard:configure")}</button><button class="text-link danger" data-remove-widget="${i}" aria-label="${tHtml("dashboard:deleteWidget", {value0:(esc(title))})}">${tHtml("dashboard:delete")}</button></div></div>`;
      }).join('') || `<p class="muted">${tHtml("dashboard:noWidgetsYet")}</p>`;
      host.querySelectorAll('[data-visible]').forEach(input=>input.onchange=()=>draft.instances[Number(input.dataset.visible)].hidden=!input.checked);
      host.querySelectorAll('[data-configure]').forEach(button=>button.onclick=()=>{const id=draft.instances[Number(button.dataset.configure)].id;configureWidget(draft.instances[Number(button.dataset.configure)],ctx,async(next)=>{const index=draft.instances.findIndex(item=>item.id===id);if(index>=0)draft.instances[index]=next;list();});});
      host.querySelectorAll('[data-remove-widget]').forEach(button=>button.onclick=()=>{draft.instances.splice(Number(button.dataset.removeWidget),1);list();});
    };
    $('[data-add-widget]',host).onclick=()=>{
      const id=$('[data-widget-type]',host).value,spec=widgetRegistry.get(id);
      if(!spec || !ctx.moduleEnabled(spec.moduleId))return ctx.toast(tText("dashboard:enableTheCorrespondingSectionInTheSettings"));
      if(draft.instances.length>=30)return ctx.toast(tText("dashboard:youCanAddUpTo30Widgets"));
      draft.instances.push({id:'w_'+crypto.randomUUID(),module_id:spec.moduleId,title:'',hidden:false,size:spec.size,config:structuredClone(spec.defaults || {})});list();
    };
    $('[data-save-layout]',host).onclick=async()=>{
      const button=$('[data-save-layout]',host);button.disabled=true;
      try{const saved=await ctx.api('/dashboard/layout','PUT',draft);ctx.state.me.settings.dashboard=saved;changed(saved);$('.modal-close',el).click();ctx.toast(tText("dashboard:mainScreenConfigured"));}catch(error){ctx.toast(error.message);}finally{button.disabled=false;}
    };list();
  }catch(error){if(el.isConnected)$('[data-manager]',el).textContent=error.message;}
}
