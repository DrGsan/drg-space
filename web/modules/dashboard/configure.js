import {tText,tHtml} from "../../core/i18n.js";
import { widgetSize, sizeOptions } from './sizes.js';
import { configureClock } from "./clock-configure.js";
import { configureWeather } from "./weather-configure.js";
import {configureTasks} from './task-configure.js';
import { $, esc, formBind } from "../../core/ui.js";

export function configureWidget(instance, ctx, save) {
  const draft = structuredClone(instance);
  const module = ctx.state.moduleCatalog.find((item) => item.id === draft.module_id);
  const el = ctx.modal(`<h2>${tHtml("dashboard:widgetSettings")}</h2><p class="muted">${esc(module.title)}</p><form data-widget-form><label>${tHtml("dashboard:widgetName")}<input name="title" maxlength="100" value="${esc(draft.title)}" placeholder="${esc(module.title)}"></label><label>${tHtml("dashboard:size")}<select name="size" aria-label="${tHtml("dashboard:size")}">${sizeOptions(draft.module_id)}</select></label><div data-options></div><button class="primary" type="submit">${tHtml("dashboard:saveWidget")}</button></form>`);
  $('[name=size]',el).value=widgetSize(draft.size);
  const host=$('[data-options]',el);
  let read = () => draft.config;
  if(draft.module_id==='calendar'){
    host.innerHTML=['production','thematic'].map(key=>`<label class="check-label"><input type="checkbox" name="${key}" ${draft.config[key]!==false?'checked':''}>${tHtml('dashboard:'+(key==='production'?'productionCalendar':'thematicCalendar'))}</label>`).join('');
    read=()=>({production:$('[name=production]',host).checked,thematic:$('[name=thematic]',host).checked});
  }else if(draft.module_id==='tasks'){
    read=configureTasks(host,draft,el,ctx).read;
  }else if(draft.module_id==='favorites'){
    host.innerHTML=`<label>${tHtml("dashboard:linkColumns")}<select name=columns><option value=auto>${tHtml("dashboard:byWidgetWidth")}</option><option value=1>${tHtml("dashboard:one")}</option><option value=2>${tHtml("dashboard:two")}</option><option value=3>${tHtml("dashboard:three")}</option></select></label>`;
    $('[name=columns]',host).value=draft.config.columns||'auto';read=()=>({columns:$('[name=columns]',host).value});
  }else if(draft.module_id==='authenticator'){
    const selected=new Set(draft.config.entry_ids||[]);
    host.innerHTML=`<p class="hint">${tHtml("dashboard:selectTheServicesForThisWidgetIfNone")}</p><div data-vault-options></div>`;
    ctx.api('/authenticators').then(items=>{if(!el.isConnected)return;$('[data-vault-options]',host).innerHTML=items.map(x=>`<label class="check-label"><input type="checkbox" data-vault-id="${x.id}" ${selected.has(x.id)?'checked':''}>${esc(x.label)}</label>`).join('');host.querySelectorAll('[data-vault-id]').forEach(b=>b.onchange=()=>{if(b.checked)selected.add(b.dataset.vaultId);else selected.delete(b.dataset.vaultId);});}).catch(e=>{if(el.isConnected)$('[data-vault-options]',host).textContent=e.message;});
    read=()=>({entry_ids:[...selected]});
  }else if(draft.module_id==='expenses'){
    host.innerHTML=`<label>${tHtml("dashboard:namesForTheNewEveningEachOnA")}<textarea name="people" rows="3" maxlength="2400">${esc((draft.config.participants||[tText("dashboard:volodya")]).join('\n'))}</textarea></label><label>${tHtml("dashboard:saveHistory")}<select name="retention"><option value="7">${tHtml("dashboard:label7Days")}</option><option value="30">${tHtml("dashboard:label30Days")}</option><option value="90">${tHtml("dashboard:label90Days")}</option></select></label>`;
    $('[name=retention]',host).value=String(draft.config.retention_days||30);
    read=()=>({participants:$('[name=people]',host).value.split('\n').map(x=>x.trim()).filter(Boolean),retention_days:Number($('[name=retention]',host).value)});
  }else if(draft.module_id==='clock'){
    const clock=configureClock(host,draft,el,ctx);read=clock.read;
  } else if(draft.module_id==='chat'){
    const spaces=ctx.state.spaces||[];
    host.innerHTML=`<label>${tHtml("dashboard:conversation")}<select name="space_id"><option value="">${tHtml("dashboard:allContactsAndWorkspaces")}</option>${spaces.map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('')}</select></label><p class="hint">${tHtml("dashboard:youCanAddMultipleWidgetsAndChooseOne")}</p>`;
    $('[name=space_id]',host).value=draft.config.space_id||'';
    read=()=>({space_id:$('[name=space_id]',host).value||null});
  } else if (draft.module_id === "weather") {
    const weather=configureWeather(host,draft,el,ctx);read=weather.read;
  } else if (draft.module_id === "rss") {
    const feeds=structuredClone(draft.config.feeds || []);
    host.innerHTML=`<label>${tHtml("dashboard:numberOfHeaders")}<input name="limit" type="number" min="1" max="50" value="${draft.config.limit || 10}"></label><p class="hint">${tHtml("dashboard:addADirectRssAtomUrlOrA")}</p><div data-feed-list></div><button type="button" class="secondary" data-add-feed>${tHtml("dashboard:addSource")}</button>`;
    const list=()=>{
      $('[data-feed-list]',host).innerHTML=feeds.map((feed,i)=>`<div class="config-row"><label>${tHtml("dashboard:sourceName")}<input data-label="${i}" value="${esc(feed.label || '')}" maxlength="100"></label><label>${tHtml("dashboard:sourceUrl")}<input type="url" data-url="${i}" value="${esc(feed.url)}" placeholder="https://example.com/feed.xml" required></label><button type="button" class="text-link danger" data-remove="${i}" aria-label="${tHtml("dashboard:deleteSource", {value0:(i+1)})}">${tHtml("dashboard:delete")}</button></div>`).join('');
      host.querySelectorAll('[data-label]').forEach(input=>input.oninput=()=>feeds[Number(input.dataset.label)].label=input.value);
      host.querySelectorAll('[data-url]').forEach(input=>input.oninput=()=>feeds[Number(input.dataset.url)].url=input.value);
      host.querySelectorAll('[data-remove]').forEach(button=>button.onclick=()=>{feeds.splice(Number(button.dataset.remove),1);list();});
    };
    $('[data-add-feed]',host).onclick=()=>{if(feeds.length>=10)return ctx.toast(tText("dashboard:youCanAddUpTo10Sources"));feeds.push({label:'',url:''});list();};list();
    read=()=>({feeds,limit:Number($('[name=limit]',host).value)});
  } else if (draft.module_id === "bookmarks") {
    const links=structuredClone(draft.config.links || []);
    host.innerHTML=`<div data-link-list></div><button type="button" class="secondary" data-add-link>${tHtml("dashboard:addLabel")}</button>`;
    const list=()=>{
      $('[data-link-list]',host).innerHTML=links.map((link,i)=>`<div class="config-row"><label>${tHtml("dashboard:websiteName")}<input data-title="${i}" value="${esc(link.title)}" maxlength="100" required></label><label>${tHtml("dashboard:websiteUrl")}<input data-url="${i}" type="url" value="${esc(link.url)}" placeholder="https://example.com" required></label><div class="duration-fields"><label>${tHtml("dashboard:icon")}<input data-icon="${i}" value="${esc(link.icon || 'auto')}" maxlength="16"></label><label>${tHtml("dashboard:color")}<select data-color="${i}">${['blue','sage','amber','lilac','rose','slate'].map((color,j)=>`<option value="${color}" ${link.color===color?'selected':''}>${[tText("dashboard:blue"),tText("dashboard:green"),tText("dashboard:beige"),tText("dashboard:lavender"),tText("dashboard:pink"),tText("dashboard:gray")][j]}</option>`).join('')}</select></label></div><button type="button" class="text-link danger" data-remove="${i}" aria-label="${tHtml("dashboard:deleteLabel", {value0:(i+1)})}">${tHtml("dashboard:delete")}</button></div>`).join('');
      for(const field of ['title','url','icon','color'])host.querySelectorAll('[data-'+field+']').forEach(input=>input.oninput=()=>links[Number(input.dataset[field])][field]=input.value);
      host.querySelectorAll('[data-remove]').forEach(button=>button.onclick=()=>{links.splice(Number(button.dataset.remove),1);list();});
    };
    $('[data-add-link]',host).onclick=()=>{if(links.length>=40)return ctx.toast(tText("dashboard:youCanAddUpTo40Labels"));links.push({title:'',url:'',icon:'auto',color:'blue'});list();};list();read=()=>({links});
  } else if (draft.module_id === "media") {
    const config={items:[],selected:[],mode:'static',interval:10,fit:'cover',...draft.config};
    host.innerHTML=`<div class="duration-fields"><label>${tHtml("dashboard:displayMode")}<select name="mode"><option value="static">${tHtml("dashboard:singleImageOrGif")}</option><option value="slideshow">${tHtml("dashboard:slideshow")}</option></select></label><label>${tHtml("dashboard:changeEverySeconds")}<input name="interval" type="number" min="2" max="86400" value="${config.interval}"></label></div><label>${tHtml("dashboard:placement")}<select name="fit"><option value="cover">${tHtml("dashboard:fillTheBlock")}</option><option value="contain">${tHtml("dashboard:showCompletely")}</option></select></label><p class="hint">${tHtml("dashboard:markImagesToDisplayInSingleViewMode")}</p><div data-media-list></div><label>${tHtml("dashboard:httpsLinkToImageOrGif")}<input data-media-url type="url" placeholder="https://example.com/image.gif"></label><button type="button" class="secondary" data-add-url>${tHtml("dashboard:addViaLink")}</button><label>${tHtml("dashboard:uploadImagesOrGif")}<input data-media-files type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple></label><p class="hint">${tHtml("dashboard:upTo50MbPerFileUploadedFiles")}</p><div data-library></div>`;
    $('[name=mode]',host).value=config.mode;$('[name=fit]',host).value=config.fit;
    const list=()=>{
      $('[data-media-list]',host).innerHTML=config.items.map((item,i)=>`<div class="media-config-row"><img src="${esc(item.asset_id?'/api/media/'+item.asset_id:item.url)}" alt="" loading="lazy"><label class="check-label"><input type="checkbox" data-selected="${i}" ${config.selected.includes(item.id)?'checked':''}>${tHtml("dashboard:showImage", {value0:(i+1)})}</label><label>${tHtml("dashboard:caption")}<input data-caption="${i}" value="${esc(item.title)}" maxlength="100"></label><button type="button" class="text-link danger" data-remove="${i}" aria-label="${tHtml("dashboard:removeImage", {value0:(i+1)})}">${tHtml("dashboard:remove")}</button></div>`).join('');
      host.querySelectorAll('[data-selected]').forEach(input=>input.onchange=()=>{const id=config.items[Number(input.dataset.selected)].id;config.selected=input.checked?[...config.selected,id]:config.selected.filter(value=>value!==id);});
      host.querySelectorAll('[data-caption]').forEach(input=>input.oninput=()=>config.items[Number(input.dataset.caption)].title=input.value);
      host.querySelectorAll('[data-remove]').forEach(button=>button.onclick=()=>{const [removed]=config.items.splice(Number(button.dataset.remove),1);config.selected=config.selected.filter(id=>id!==removed.id);list();});
    };
    const add=(asset)=>{if(config.items.length>=30)throw new Error(tText("dashboard:youCanAddUpTo30Images"));const item={id:'m_'+crypto.randomUUID(),title:'',...asset};config.items.push(item);config.selected.push(item.id);list();};
    $('[data-add-url]',host).onclick=()=>{
      try{const url=$('[data-media-url]',host).value;const parsed=new URL(url);if(parsed.protocol!=='https:'||parsed.username||parsed.password)throw new Error(tText("dashboard:requiresAHttpsLink"));add({url});$('[data-media-url]',host).value='';}catch(error){ctx.toast(error.message);}
    };
    const library=async()=>{
      const assets=await ctx.api('/media');if(!el.isConnected)return;
      $('[data-library]',host).innerHTML=`<h3>${tHtml("dashboard:uploadedFiles")}</h3>${assets.map((asset)=>`<div class="library-row"><img src="${esc(asset.url)}" alt="" loading="lazy"><span class="small muted">${asset.width}×${asset.height}${asset.frames>1?(" "+tText("dashboard:gifAnimation")+""):''}</span><button type="button" class="text-link" data-use="${asset.id}">${tHtml("dashboard:addToWidget")}</button><button type="button" class="text-link danger" data-delete-file="${asset.id}">${tHtml("dashboard:deleteFile")}</button></div>`).join('')}`;
      host.querySelectorAll('[data-use]').forEach(button=>button.onclick=()=>{try{add({asset_id:button.dataset.use});}catch(error){ctx.toast(error.message);}});
      host.querySelectorAll('[data-delete-file]').forEach(button=>button.onclick=async()=>{try{if(config.items.some(item=>item.asset_id===button.dataset.deleteFile))throw new Error(tText("dashboard:firstRemoveTheFileFromTheWidget"));await ctx.api('/media/'+button.dataset.deleteFile,'DELETE');await library();}catch(error){ctx.toast(error.message);}});
    };
    $('[data-media-files]',host).onchange=async(event)=>{
      const input=event.target;input.disabled=true;
      try{for(const file of input.files){if(file.size>50*1024*1024)throw new Error(tText("dashboard:theFileMustBeNoMoreThan50"));const asset=await ctx.api('/media','POST',file);if(el.isConnected)add({asset_id:asset.id,title:file.name.slice(0,100)});}await library();}catch(error){ctx.toast(error.message);}finally{input.disabled=false;input.value='';}
    };
    list();library().catch(error=>ctx.toast(error.message));
    read=()=>({...config,mode:$('[name=mode]',host).value,interval:Number($('[name=interval]',host).value),fit:$('[name=fit]',host).value});
  } else host.innerHTML=`<p class="muted">${tHtml("dashboard:thisWidgetUsesYourAccountSSharedData")}</p>`;
  formBind($('[data-widget-form]',el),async(form)=>{
    draft.title=String(form.get('title') || '').trim();draft.size=form.get('size');draft.config=read();
    await save(draft);$('.modal-close',el).click();
  });
}
