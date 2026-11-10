import {tText,tHtml} from "../../core/i18n.js";
import { $, esc, formBind } from '../../core/ui.js';
import { appearanceDefaults, themes, wallpaperCategories, wallpaperCategory, wallpaperPresets, wallpapers, setAppearance, wallpaperBackground } from '../../core/appearance.js';
import { prepareWallpaper } from './wallpaper.js';
import {seasonalThemes,setSeasonalPreview,getSeasonalPreview} from '../../core/seasonal.js';
import './settings.css';

export function mountAppearanceSettings(host,ctx){
  let disposed=false,pendingBlob=null,pendingUrl='',fileSequence=0,revision=0,preparing=false,saving=false;
  const config={...appearanceDefaults,...ctx.state.me.settings.appearance};
  let committed={...config},category=wallpaperCategory(config.wallpaper);
  const wallpaperChoice=([id,title])=>`<button type="button" class="wallpaper-choice" data-wallpaper="${id}" aria-pressed="${config.wallpaper===id}"><span class="wallpaper-swatch" aria-hidden="true"></span><span>${esc(title)}</span></button>`;
  host.style.setProperty('--appearance-warm',wallpaperBackground('warm'));
  host.innerHTML=`<h2>${tHtml("appearance:appearance")}</h2><p class="muted">${tHtml("appearance:eachThemeHasALightAndDarkPalette")}</p><p class="hint">${tHtml("appearance:seeHowYourChoiceLooksSaveAppearanceWill")}</p>
    <form data-appearance><div class="appearance-layout"><div class="appearance-controls">
      <fieldset class="appearance-fieldset"><legend>${tHtml("appearance:colorTheme")}</legend><div class="theme-choices">${Object.entries(themes).map(([id,title])=>`<label class="theme-choice theme-${id}"><input type="radio" name="theme" value="${id}" ${config.theme===id?'checked':''}><span class="theme-swatch" aria-hidden="true"></span>${esc(title)}</label>`).join('')}</div></fieldset>
      <fieldset class="appearance-fieldset"><legend>${tHtml("appearance:interfaceStyle")}</legend><div class="appearance-styles">${[['classic',tText("appearance:classic")],['glass',tText("appearance:glass")]].map(([id,title])=>`<label class="appearance-style"><span><input type="radio" name="style" value="${id}" ${config.style===id?'checked':''}>${title}</span><span class="appearance-style-picture appearance-style-${id}" aria-hidden="true"><i></i><i></i><i></i></span></label>`).join('')}</div><p class="hint" data-wallpaper-hint ${config.style==='glass'?'hidden':''}>${tHtml("appearance:wallpaperGlassOnlyHint")}</p></fieldset>
      <label>${tHtml("appearance:appearanceMode")}<select name="mode"><option value="light">${tHtml("appearance:alwaysLight")}</option><option value="dark">${tHtml("appearance:alwaysDark")}</option><option value="schedule">${tHtml("appearance:dayAndNightBasedOnTime")}</option><option value="system">${tHtml("appearance:asOnTheDevice")}</option></select></label>
      <div class="duration-fields" data-schedule><label>${tHtml("appearance:dayStarts")}<input name="day_start" type="time" value="${config.day_start}" required></label><label>${tHtml("appearance:nightStarts")}<input name="night_start" type="time" value="${config.night_start}" required></label></div>
      <p class="hint">${tHtml("appearance:theScheduleUsesYourDeviceSLocalTime")}</p>
      <fieldset class="appearance-seasonal"><legend>${tHtml("appearance:seasonalEffects")}</legend>
        <p class="hint">${tHtml("appearance:seasonalDescription")}</p>
        <select name="seasonal" aria-label="${tHtml("appearance:seasonalEffects")}"><option value="auto">${tHtml("appearance:seasonalAuto")}</option><option value="off">${tHtml("appearance:seasonalOff")}</option></select>
        <p class="hint">${tHtml("appearance:seasonalCalendar")}</p>
        <label>${tHtml("appearance:seasonalPreview")}<select data-seasonal-preview><option value="">${tHtml("appearance:seasonalToday")}</option>${seasonalThemes.map(theme=>`<option value="${theme.id}">${esc(tText('appearance:'+theme.name_key))}</option>`).join('')}</select></label>
        <div class="seasonal-preview-header"><strong>DrG Space</strong><span>${tHtml("appearance:seasonalPreview")}</span></div>
        <p class="hint">${tHtml("appearance:seasonalPreviewHint")}</p>
      </fieldset>
      <fieldset class="appearance-fieldset" data-wallpapers ${config.style==='glass'?'':'hidden'}><legend>${tHtml("appearance:wallpaper")}</legend><p class="hint">${tHtml("appearance:readyBackgroundsInFourCategories")}</p><input type="hidden" name="wallpaper" value="${esc(config.wallpaper)}"><div class="wallpaper-categories" role="group" aria-label="${tHtml("appearance:wallpaperCategories")}">${wallpaperCategories.map(item=>`<button type="button" data-wallpaper-category="${item.id}" aria-pressed="${item.id===category}" aria-controls="wallpaper-panel-${item.id}">${esc(item.name)}</button>`).join('')}</div>${wallpaperCategories.map(item=>`<div class="wallpaper-choices" id="wallpaper-panel-${item.id}" data-wallpaper-panel="${item.id}" role="group" aria-label="${esc(item.name)}" ${item.id===category?'':'hidden'}>${item.items.map(preset=>wallpaperChoice([preset.id,preset.name])).join('')}</div>`).join('')}<div class="wallpaper-alternatives" role="group" aria-label="${tHtml("appearance:otherBackgrounds")}">${Object.entries(wallpapers).filter(([id])=>!Object.hasOwn(wallpaperPresets,id)).map(wallpaperChoice).join('')}</div>
      <div class="wallpaper-custom" data-custom-wallpaper hidden><span class="wallpaper-custom-image" aria-hidden="true"></span><span>${tHtml("appearance:yourImage")}</span><button type="button" class="text-link" data-reset-wallpaper>${tHtml("appearance:remove")}</button></div>
      <label class="secondary wallpaper-upload">${tHtml("appearance:chooseImage")}<input type="file" accept="image/png,image/jpeg,image/webp,image/gif" data-wallpaper-file aria-label="${tHtml("appearance:imageForWallpaper")}"></label><p class="hint">${tHtml("appearance:pngJpegWebpOrGifUpTo50")}</p><p class="hint" role="status" data-wallpaper-status></p></fieldset>
      <div class="appearance-actions"><button class="primary" type="submit">${tHtml("appearance:saveLayout")}</button><button class="text-link" type="button" data-appearance-reset>${tHtml("appearance:cancelChanges")}</button></div><p class="hint" role="status" data-appearance-status></p>
    </div><aside class="appearance-preview" aria-label="${tHtml("appearance:previewLayout")}"><div class="appearance-preview-scene"><div class="appearance-preview-rail"><img src="/assets/icons/drg-d8-logo.png" alt="" width="26" height="26"><i></i><i></i><i></i></div><div class="appearance-preview-main"><div class="appearance-preview-heading"><strong>${tHtml("appearance:hello", {value0:(esc(ctx.state.me.settings.profile?.display_name||ctx.state.me.login))})}</strong><span class="appearance-preview-subtitle">${tHtml("appearance:everythingYouNeedAtYourFingertips")}</span></div><div class="appearance-preview-cards"><div><b>${tHtml("appearance:reminders")}</b><span>${tHtml("appearance:buyGroceries")}</span><span>${tHtml("appearance:weeklyPlan")}</span></div><div><b>${tHtml("appearance:lastNotes")}</b><span>${tHtml("appearance:weeklyPlan2")}</span><span>${tHtml("appearance:homeIdeas")}</span></div><div><b>${tHtml("appearance:calendar")}</b><span class="appearance-preview-date">${new Date().getDate()}</span></div><div><b>${tHtml("appearance:weather")}</b><span class="appearance-preview-date">☀ 12°</span></div></div></div></div><p class="hint">${tHtml("appearance:previewYourSectionsAndWidgetLayoutAreSaved")}</p></aside></div></form>`;
  $('.appearance-preview',host).append($('[data-wallpapers]',host));
  $('.appearance-layout',host).append($('.appearance-actions',host),$('[data-appearance-status]',host));
  const form=$('[data-appearance]',host),wallpaper=$('[name=wallpaper]',host),status=$('[data-wallpaper-status]',host);
  const submit=$('[type=submit]',form);
  const showCategory=value=>{
    category=value;
    for(const button of host.querySelectorAll('[data-wallpaper-category]'))button.setAttribute('aria-pressed',String(button.dataset.wallpaperCategory===category));
    for(const panel of host.querySelectorAll('[data-wallpaper-panel]'))panel.hidden=panel.dataset.wallpaperPanel!==category;
  };
  $('[name=mode]',host).value=config.mode;
  $('[name=seasonal]',host).value=config.seasonal;
  const holidayPreview=$('[data-seasonal-preview]',host);
  const syncHolidayPreview=()=>{holidayPreview.value=getSeasonalPreview();};
  syncHolidayPreview();window.addEventListener('drg-seasonal-preview',syncHolidayPreview);
  holidayPreview.onchange=()=>setSeasonalPreview(holidayPreview.value);
  for(const button of host.querySelectorAll('[data-wallpaper]'))button.querySelector('.wallpaper-swatch').style.backgroundImage=wallpaperBackground(button.dataset.wallpaper);
  const values=()=>Object.fromEntries(new FormData(form));
  const clearImage=()=>{preparing=false;submit.disabled=saving;pendingBlob=null;if(pendingUrl)URL.revokeObjectURL(pendingUrl);pendingUrl='';$('[data-wallpaper-file]',host).value='';};
  const preview=()=>{
    const draft=values();$('[data-schedule]',host).hidden=draft.mode!=='schedule';
    $('[data-wallpapers]',host).hidden=draft.style!=='glass';
    $('[data-wallpaper-hint]',host).hidden=draft.style==='glass';
    setAppearance(draft,false,pendingUrl);
    const scene=$('.appearance-preview-scene',host);scene.dataset.style=draft.style;
    scene.style.backgroundImage=pendingUrl?`url("${pendingUrl}")`:wallpaperBackground(draft.wallpaper);
    for(const button of host.querySelectorAll('[data-wallpaper]'))button.setAttribute('aria-pressed',String(!pendingBlob&&button.dataset.wallpaper===draft.wallpaper));
    const custom=$('[data-custom-wallpaper]',host);custom.hidden=!pendingBlob&&Object.hasOwn(wallpapers,draft.wallpaper);
    $('.wallpaper-custom-image',host).style.backgroundImage=pendingUrl?`url("${pendingUrl}")`:wallpaperBackground(draft.wallpaper);
  };
  const changed=()=>{revision++;$('[data-appearance-status]',host).textContent=tText("appearance:previewChangesHaveNotBeenSavedYet");preview();};
  form.addEventListener('input',event=>{if(event.target.matches('[data-wallpaper-file],[data-seasonal-preview]'))return;changed();});
  form.addEventListener('change',event=>{if(event.target.matches('[data-wallpaper-file],[data-seasonal-preview]'))return;changed();});
  for(const button of host.querySelectorAll('[data-wallpaper-category]'))button.onclick=()=>showCategory(button.dataset.wallpaperCategory);
  for(const button of host.querySelectorAll('[data-wallpaper]'))button.onclick=()=>{fileSequence++;clearImage();wallpaper.value=button.dataset.wallpaper;status.textContent='';changed();};
  $('[data-reset-wallpaper]',host).onclick=()=>{fileSequence++;clearImage();wallpaper.value='warm';showCategory(wallpaperCategory('warm'));status.textContent='';changed();};
  $('[data-wallpaper-file]',host).onchange=async event=>{
    const file=event.target.files[0];if(!file)return;
    const sequence=++fileSequence;preparing=true;submit.disabled=true;status.textContent=tText("appearance:preparingImage");
    try{
      const blob=await prepareWallpaper(file);if(disposed||sequence!==fileSequence)return;
      clearImage();pendingBlob=blob;pendingUrl=URL.createObjectURL(blob);
      status.textContent=tText("appearance:imageIsReadyClickSaveLayout");changed();
    }catch(error){if(!disposed&&sequence===fileSequence)status.textContent=error.message;}
    finally{if(!disposed&&sequence===fileSequence){preparing=false;submit.disabled=saving;}}
  };
  $('[data-appearance-reset]',host).onclick=()=>{
    fileSequence++;clearImage();form.reset();
    syncHolidayPreview();
    for(const name of ['theme','style'])$(`[name=${name}][value="${committed[name]}"]`,host).checked=true;
    for(const name of ['mode','day_start','night_start','wallpaper','seasonal'])$(`[name=${name}]`,host).value=committed[name];
    showCategory(wallpaperCategory(committed.wallpaper));status.textContent='';revision++;$('[data-appearance-status]',host).textContent=tText("appearance:changesCancelled");preview();
  };
  preview();
  formBind(form,async data=>{
    if(preparing)throw Error(tText("appearance:waitForTheImageToBePrepared"));
    saving=true;
    const submitted=Object.fromEntries(data),blob=pendingBlob,submittedRevision=revision;let uploadedId;
    try{
      if(blob){const image=await ctx.api('/media','POST',blob);uploadedId=image.id;submitted.wallpaper=image.id;}
      const saved=await ctx.api('/appearance','PUT',submitted);
      committed=saved;if(ctx.state.me)ctx.state.me.settings.appearance=saved;setAppearance(saved);
      if(!disposed){
        if(revision===submittedRevision){clearImage();wallpaper.value=saved.wallpaper;status.textContent='';$('[data-appearance-status]',host).textContent=tText("appearance:layoutSaved");}
        preview();
      }
      ctx.toast(tText("appearance:layoutSaved"));
    }catch(error){
      // A failed preferences write must not strand a newly staged upload.
      // The media endpoint protects anything already referenced by a setting.
      if(uploadedId)try{await ctx.api('/media/'+uploadedId,'DELETE');}catch{}
      throw error;
    }finally{
      saving=false;
      // formBind restores the submit button after our promise settles. A new
      // file may still be decoding if it was selected while Save was running.
      if(!disposed&&preparing)requestAnimationFrame(()=>{if(!disposed)submit.disabled=preparing;});
    }
  });
  return ()=>{disposed=true;fileSequence++;clearImage();window.removeEventListener('drg-seasonal-preview',syncHolidayPreview);setAppearance(ctx.state.me?.settings.appearance??committed);};
}
