import {tText,tHtml} from "../../core/i18n.js";
import {language} from '../../core/i18n.js';
const catalogs={
  ru:()=>Promise.all([import('emojibase-data/ru/data.json'),import('emojibase-data/ru/messages.json')]),
  en:()=>Promise.all([import('emojibase-data/en/data.json'),import('emojibase-data/en/messages.json')]),
};
import {$,esc} from '../../core/ui.js';
export async function iconPicker(workspace,options={}){
  let data;try{data=await (catalogs[language()]||catalogs.en)();}catch(error){workspace.ctx.toast(error.message);return;}
  if(workspace.disposed)return;
  const [emoji,messages]=data.map(value=>value.default),all=emoji.filter(x=>x.group!==undefined&&x.emoji);
  const ctx=workspace.ctx,el=ctx.modal(`<h2>${tHtml("notes:pageIcon")}</h2><div class=decoration-tabs><button class="tab active" data-decoration-tab=emoji>${tHtml("notes:emoji")}</button><button class=tab data-decoration-tab=upload>${tHtml("notes:uploadIcon")}</button></div><section data-emoji-picker><label>${tHtml("notes:searchForEmoji")}<input data-emoji-search type=search placeholder="${tHtml("notes:sunCatBook")}"></label><label>${tHtml("notes:skinTone")}<select data-emoji-tone><option value=0>${tHtml("notes:default")}</option><option value=1>${tHtml("notes:light")}</option><option value=2>${tHtml("notes:light2")}</option><option value=3>${tHtml("notes:medium")}</option><option value=4>${tHtml("notes:dark")}</option><option value=5>${tHtml("notes:dark2")}</option></select></label><div class=emoji-categories>${messages.groups.map(g=>`<button class="secondary compact" data-emoji-category="${g.order}">${esc(g.message)}</button>`).join('')}</div><div class=emoji-results></div></section><section data-icon-upload hidden><label>${tHtml("notes:iconFile")}<input data-icon-file type=file accept="image/png,image/jpeg,image/webp,image/gif"></label><p class=hint>${tHtml("notes:upTo20MbATransparent96X96Thumbnail")}</p><p data-icon-status role=status></p></section>`);
  let group=0;
  if(options.title)el.querySelector('h2').textContent=options.title;
  if(options.emojiOnly){$('[data-decoration-tab=upload]',el).hidden=true;}
  const render=()=>{
    const query=$('[data-emoji-search]',el).value.toLocaleLowerCase().trim(),tone=Number($('[data-emoji-tone]',el).value);
    const items=all.filter(e=>query?(e.label+' '+(e.tags||[]).join(' ')).toLocaleLowerCase().includes(query):e.group===group);
    $('.emoji-results',el).innerHTML=items.map(e=>{const variant=tone&&e.skins?.find(s=>s.tone===tone||(Array.isArray(s.tone)&&s.tone.every(t=>t===tone)));const value=variant?.emoji||e.emoji;return `<button class=emoji-option title="${esc(e.label)}" aria-label="${esc(e.label)}" data-emoji="${esc(value)}">${esc(value)}</button>`;}).join('')||`<p class=muted>${tHtml("notes:nothingFound")}</p>`;
    el.querySelectorAll('[data-emoji]').forEach(b=>b.onclick=async()=>{if(options.onPick)await options.onPick(b.dataset.emoji);else await workspace.meta({icon:b.dataset.emoji});$('.modal-close',el).click();});
  };
  $('[data-emoji-search]',el).oninput=render;$('[data-emoji-tone]',el).onchange=render;
  el.querySelectorAll('[data-emoji-category]').forEach(b=>b.onclick=()=>{group=Number(b.dataset.emojiCategory);$('[data-emoji-search]',el).value='';render();});
  el.querySelectorAll('[data-decoration-tab]').forEach(b=>b.onclick=()=>{$('[data-emoji-picker]',el).hidden=b.dataset.decorationTab!=='emoji';$('[data-icon-upload]',el).hidden=b.dataset.decorationTab!=='upload';el.querySelectorAll('[data-decoration-tab]').forEach(t=>t.classList.toggle('active',t===b));});
  $('[data-icon-file]',el).onchange=async e=>{const file=e.target.files[0];if(!file)return;e.target.disabled=true;try{if(file.size>20*1024*1024)throw Error(tText("notes:iconIsNoMoreThan20Mb"));if(!(await workspace.persistDraft())||!(await workspace.flush()))return;await ctx.api('/notes/'+workspace.page.id+'/assets/icon','POST',file);await workspace.refresh();await workspace.open(workspace.page.id,false);$('.modal-close',el).click();}catch(error){$('[data-icon-status]',el).textContent=error.message;}finally{e.target.disabled=false;}};
  render();
}
export function coverPicker(workspace){
  const ctx=workspace.ctx,el=ctx.modal(`<h2>${tHtml("notes:pageCover")}</h2><div class=decoration-tabs><button class="tab active" data-cover-tab=photos>${tHtml("notes:photos")}</button><button class=tab data-cover-tab=upload>${tHtml("notes:upload")}</button><button class=tab data-cover-tab=colors>${tHtml("notes:colors")}</button></div><section data-cover-photos><div class=duration-fields><label>${tHtml("notes:searchForPhotos")}<input data-cover-search type=search placeholder="${tHtml("notes:mountainsSeaVladivostok")}" maxlength=120></label><label>${tHtml("notes:gallery2")}<select data-cover-provider><option value=commons>Wikimedia Commons</option></select></label></div><p class=hint data-cover-provider-hint hidden>${tHtml("notes:unsplashIsAvailableAfterConfiguringTheApiKey")}</p><div class=cover-photo-grid></div><p data-cover-status role=status></p></section><section data-cover-upload hidden><label>${tHtml("notes:coverImage")}<input data-cover-file type=file accept="image/png,image/jpeg,image/webp,image/gif"></label><p class=hint>${tHtml("notes:upTo20MbTheImageIsConverted")}</p></section><section data-cover-colors hidden><div class=cover-picker>${['blue','sage','sand','rose','night',''].map(c=>`<button class="${c?'cover-'+c:''}" data-cover-color="${c}" aria-label="${c||tText("notes:removeCover")}">${c?'':tText("notes:withoutCover")}</button>`).join('')}</div></section><button class="text-link danger" data-remove-cover>${tHtml("notes:removeCover")}</button>`);
  let timer,sequence=0;
  const save=async fn=>{if(!(await workspace.persistDraft())||!(await workspace.flush()))return;await fn();await workspace.refresh();await workspace.open(workspace.page.id,false);$('.modal-close',el).click();};
  const grid=$('.cover-photo-grid',el),status=$('[data-cover-status]',el),provider=$('[data-cover-provider]',el);
  const search=async()=>{
    if(!el.isConnected)return;
    clearTimeout(timer);
    const seq=++sequence;
    grid.replaceChildren();status.textContent=tText("notes:searchingForPhotos");
    try{
      const data=await ctx.api('/note-cover-search?q='+encodeURIComponent($('[data-cover-search]',el).value)+'&provider='+provider.value);
      if(!el.isConnected||seq!==sequence)return;
      if(data.providers){
        const selected=provider.value;
        provider.innerHTML=data.providers.map(p=>`<option value="${esc(p)}">${p==='commons'?'Wikimedia Commons':'Unsplash'}</option>`).join('');
        provider.value=data.providers.includes(selected)?selected:'commons';
        $('[data-cover-provider-hint]',el).hidden=data.providers.includes('unsplash');
      }
      grid.innerHTML=data.items.map((p,i)=>`<button class=cover-photo data-cover-choice="${i}" disabled title="${esc(p.attribution.author+' · '+p.attribution.license)}"><img src="${esc(p.thumbnail)}" alt="${esc(p.title)}" loading=lazy referrerpolicy=no-referrer><span>${esc(p.attribution.author)} · ${esc(p.attribution.license)}</span></button>`).join('');
      const updateStatus=()=>{
        if(!el.isConnected||seq!==sequence)return;
        status.textContent=grid.childElementCount?tText("notes:selectAPhotoTheAuthorAndLicenseWill"):data.items.length?tText("notes:failedToUploadPhotoTryADifferentRequest"):tText("notes:noPhotosFoundTryADifferentRequest");
      };
      grid.querySelectorAll('[data-cover-choice]').forEach(b=>{
        const img=$('img',b);
        img.onload=()=>{b.disabled=false;};
        img.onerror=()=>{b.remove();updateStatus();};
        if(img.complete){if(img.naturalWidth)b.disabled=false;else img.onerror();}
        b.onclick=async()=>{
          b.disabled=true;
          try{await save(()=>ctx.api('/notes/'+workspace.page.id+'/select-cover','POST',{token:data.items[Number(b.dataset.coverChoice)].selection_token}));}
          catch(e){ctx.toast(e.message);if(e.status===422){b.remove();updateStatus();}else b.disabled=false;}
        };
      });
      updateStatus();
    }catch(e){if(el.isConnected&&seq===sequence)status.textContent=e.message;}
  };
  $('[data-cover-search]',el).oninput=()=>{clearTimeout(timer);sequence++;grid.replaceChildren();status.textContent=tText("notes:searchingForPhotos");timer=setTimeout(search,350);};
  provider.onchange=search;
  el.querySelectorAll('[data-cover-tab]').forEach(b=>b.onclick=()=>{for(const key of ['photos','upload','colors'])$('[data-cover-'+key+']',el).hidden=b.dataset.coverTab!==key;el.querySelectorAll('[data-cover-tab]').forEach(t=>t.classList.toggle('active',t===b));});
  el.querySelectorAll('[data-cover-color]').forEach(b=>b.onclick=async()=>{await workspace.meta({cover:b.dataset.coverColor});$('.modal-close',el).click();});
  $('[data-remove-cover]',el).onclick=async()=>{await workspace.meta({cover:''});$('.modal-close',el).click();};
  $('[data-cover-file]',el).onchange=async e=>{const file=e.target.files[0];if(!file)return;e.target.disabled=true;try{if(file.size>20*1024*1024)throw Error(tText("notes:coverIsNoMoreThan20Mb"));await save(()=>ctx.api('/notes/'+workspace.page.id+'/assets/cover','POST',file));}catch(error){ctx.toast(error.message);e.target.disabled=false;}};
  search();
}
