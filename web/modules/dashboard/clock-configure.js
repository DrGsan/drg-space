import {tText,tHtml,locale} from '../../core/i18n.js';
import {$,esc} from '../../core/ui.js';
import {clockDefaults,clockRegions,clockOffset,clockTimezone,clockThemes,clockTheme,clockCities,clockCapacity} from './widgets/clock-format.js';
import {mountClock,clockFace,clockDigits} from './widgets/clock.js';
import {configureWeather} from './weather-configure.js';

const themeKeys={classic:'classic',minimal:'minimalism',station:'station',roman:'romanFace',orbit:'orbitFace',bold:'boldFace',segments:'segmentsFace',flip:'flipFace',accent:'accentFace'};
export function configureClock(host,instance,modal,ctx){
  const config={...clockDefaults,...instance.config},world=Boolean(config.cities.length);
  let cities=clockCities(config).map(city=>({...city})),selected=0,cleanup,searchCleanup,disposed=false;
  const form=host.closest('form'),size=$('[name=size]',form),title=$('[name=title]',form);
  modal.querySelector('.modal')?.classList.add('clock-settings-modal');
  host.className='clock-config-layout';
  const known=clockRegions.some(([zone])=>zone===cities[0].timezone),listId='clock-timezones-'+instance.id;
  host.innerHTML=`<div class="clock-config-controls"><div class="duration-fields" data-clock-basics></div><div class="clock-mode-picker" role="group" aria-label="${tHtml('dashboard:clockView')}">${['analog','digital'].map(mode=>`<button type="button" class="secondary" data-clock-mode="${mode}" aria-pressed="false">${tHtml('dashboard:'+mode)}</button>`).join('')}</div><input type="hidden" name="clock_mode" value="${esc(config.mode)}"><input type="hidden" name="clock_theme" value="${esc(clockTheme(config.mode,config.theme))}"><fieldset class="clock-face-picker"><legend>${tHtml('dashboard:clockTheme')}</legend><div class="clock-face-gallery" data-clock-faces></div></fieldset><label class="check-label"><input name="clock_world" type="checkbox" ${world?'checked':''}>${tHtml('dashboard:worldClocks')}</label><div data-clock-cities></div><button type="button" class="text-link" data-clock-add>${tHtml('dashboard:addClockCity')}</button><details class="clock-city-disclosure"><summary>${tHtml("dashboard:findAnotherClockCity")}</summary><div data-clock-city-search></div></details><details class="clock-advanced"><summary>${tHtml('dashboard:clockAdvanced')}</summary><label>${tHtml('dashboard:clockRegion')}<select name="clock_region">${clockRegions.map(([zone,city])=>`<option value="${zone}">${esc(city)} · ${esc(clockOffset(new Date(),zone))}</option>`).join('')}<option value="custom">${tHtml('dashboard:otherRegion')}</option></select></label><div data-custom-zone><label>${tHtml('dashboard:otherTimeZone')}<input name="clock_timezone" list="${listId}" maxlength="100" placeholder="Europe/Moscow"></label><datalist id="${listId}">${(Intl.supportedValuesOf?.('timeZone')||[]).map(zone=>`<option value="${esc(zone)}"></option>`).join('')}</datalist></div><label>${tHtml('dashboard:clockDisplay')}<input name="clock_label" maxlength="100"></label><label>${tHtml('dashboard:timeFormat')}<select name="clock_hour_format"><option value="24h">${tHtml('dashboard:label24Hours')}</option><option value="12h">${tHtml('dashboard:label12HourAmPm')}</option></select></label><label>${tHtml('dashboard:dateFormat')}<select name="clock_date_format">${['dd.mm.yyyy','yyyy-mm-dd','dd/mm/yyyy','long','weekday','custom'].map((value,i)=>`<option value="${value}">${tHtml('dashboard:'+['label11102026DdMmYyyy','label20261011YyyyMmDd','label11102026DdMmYyyy2','october112026','sundayOctober112026','myFormat'][i])}</option>`).join('')}</select></label><div data-date-template><label>${tHtml('dashboard:myDateFormat')}<input name="clock_date_template" maxlength="80" value="${esc(config.date_template)}" placeholder="dddd, DD MMMM YYYY"></label><p class="hint">${tHtml('dashboard:yyyyYearYyShortYearMmMMonth')}</p></div></details><div class="clock-config-toggles">${[['seconds','showSeconds'],['show_timezone','showTimeZone'],['date','showCurrentDate']].map(([name,key])=>`<label class="check-label"><input name="clock_${name}" type="checkbox">${tHtml('dashboard:'+key)}</label>`).join('')}</div></div><div class="clock-config-preview"><span class="muted">${tHtml('dashboard:clockLivePreview')}</span><div class="clock-preview-scene"><div class="clock-preview-card"><strong data-clock-preview-title></strong><div class="clock-preview" data-clock-preview></div></div></div><p class="hint" data-clock-capacity></p><p class="hint">${tHtml('dashboard:clockPreviewUnsaved')}</p></div>`;
  $('[data-clock-basics]',host).append(title.closest('label'),size.closest('label'));
  const regionLabel=$('[name=clock_region]',host).closest('label');$('.clock-city-disclosure',host).before(regionLabel);
  $('.clock-advanced',host).before($('.clock-config-toggles',host));
  for(const [name,value] of Object.entries({clock_mode:config.mode,clock_theme:clockTheme(config.mode,config.theme),clock_region:known?cities[0].timezone:'custom',clock_hour_format:config.hour_format,clock_date_format:config.date_format}))$(`[name=${name}]`,host).value=value;
  $('[name=clock_show_timezone]',host).checked=config.show_timezone;$('[name=clock_seconds]',host).checked=config.show_seconds;$('[name=clock_date]',host).checked=config.show_date;
  function loadCity(){
    const city=cities[selected];$('[name=clock_timezone]',host).value=city.timezone;$('[name=clock_label]',host).value=city.label;
    $('[name=clock_region]',host).value=clockRegions.some(([zone])=>zone===city.timezone)?city.timezone:'custom';
    searchCleanup?.();searchCleanup=configureWeather($('[data-clock-city-search]',host),{...instance,id:instance.id+'_'+selected,config:{city:city.label||clockRegions.find(([zone])=>zone===city.timezone)?.[1]||city.timezone}},modal,ctx,{requireTimezone:true,onChoose(city){cities[selected]={timezone:city.timezone,label:city.name};$('[name=clock_region]',host).value='custom';$('[name=clock_timezone]',host).value=city.timezone;$('[name=clock_label]',host).value=city.name;cityList();preview();}}).dispose;
  }
  function storeCity(){
    const timezone=$('[name=clock_region]',host).value==='custom'?$('[name=clock_timezone]',host).value.trim():$('[name=clock_region]',host).value;
    if(!timezone)throw Error(tText('dashboard:selectTheClockRegion'));
    try{new Intl.DateTimeFormat(locale(),{timeZone:clockTimezone(timezone)});}catch{throw Error(tText('dashboard:unknownTimeZoneSelectARegionFromThe'));}
    cities[selected]={timezone,label:$('[name=clock_label]',host).value.trim()};
  }
  function read(){
    storeCity();const date_format=$('[name=clock_date_format]',host).value,date_template=$('[name=clock_date_template]',host).value;
    if(date_format==='custom'&&!date_template.trim())throw Error(tText('dashboard:enterYourDateFormat'));
    return {...config,mode:$('[name=clock_mode]',host).value,theme:$('[name=clock_theme]',host).value,...cities[0],cities:$('[name=clock_world]',host).checked?cities.map(city=>({...city})):[],show_seconds:$('[name=clock_seconds]',host).checked,show_timezone:$('[name=clock_show_timezone]',host).checked,show_date:$('[name=clock_date]',host).checked,hour_format:$('[name=clock_hour_format]',host).value,date_format,date_template:date_template||clockDefaults.date_template};
  }
  function faces(){
    const mode=$('[name=clock_mode]',host).value,theme=$('[name=clock_theme]',host).value;
    host.querySelectorAll('[data-clock-mode]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.clockMode===mode));
    $('[data-clock-faces]',host).innerHTML=clockThemes(mode).map(value=>`<button type="button" class="clock-face-choice clock-${mode} clock-theme-${value}" data-clock-theme="${value}" aria-pressed="${value===theme}"><span class="clock-face-thumbnail" aria-hidden="true">${mode==='analog'?clockFace(value,false):`<span class="clock-digits">${clockDigits(value,{hour:10,minute:10,second:0},{...config,show_seconds:false,hour_format:'24h'})}</span>`}</span><span>${tHtml('dashboard:'+(value==='station'&&mode==='digital'?'monoFace':themeKeys[value]))}</span></button>`).join('');
    host.querySelectorAll('[data-clock-theme]').forEach(b=>b.onclick=()=>{$('[name=clock_theme]',host).value=b.dataset.clockTheme;host.querySelectorAll('[data-clock-theme]').forEach(x=>x.setAttribute('aria-pressed',x===b));preview();});
  }
  function cityList(){
    const world=$('[name=clock_world]',host).checked;
    $('[data-clock-cities]',host).hidden=!world;$('[data-clock-add]',host).hidden=!world;$('[data-clock-add]',host).disabled=cities.length>=4;
    $('[data-clock-cities]',host).innerHTML=cities.map((city,i)=>`<div class="clock-config-city"><button class="secondary" type="button" data-clock-city="${i}" aria-pressed="${i===selected}">${esc(city.label||clockRegions.find(([zone])=>zone===city.timezone)?.[1]||city.timezone)}</button>${cities.length>1?`<button type="button" class="text-link danger" data-clock-remove="${i}" aria-label="${tHtml('dashboard:removeClockCity',{city:city.label||city.timezone})}">×</button>`:''}</div>`).join('');
    host.querySelectorAll('[data-clock-city]').forEach(b=>b.onclick=()=>{try{storeCity();selected=Number(b.dataset.clockCity);loadCity();cityList();preview();}catch(e){ctx.toast(e.message);}});
    host.querySelectorAll('[data-clock-remove]').forEach(b=>b.onclick=()=>{cities.splice(Number(b.dataset.clockRemove),1);selected=Math.min(selected,cities.length-1);loadCity();cityList();preview();});
  }
  function preview(){
    cleanup?.();cleanup=null;
    $('[data-custom-zone]',host).hidden=$('[name=clock_region]',host).value!=='custom';
    $('[name=clock_timezone]',host).required=$('[name=clock_region]',host).value==='custom';
    $('[data-date-template]',host).hidden=$('[name=clock_date_format]',host).value!=='custom';
    const target=$('[data-clock-preview]',host),scene=$('.clock-preview-scene',host);scene.dataset.size=size.value;
    $('[data-clock-preview-title]',host).textContent=title.value.trim()||tText('dashboard:clockWidgetTitle');
    try{const value=read();cleanup=mountClock(target,{instance:{...instance,size:size.value,config:value}});const shown=Math.min(clockCities(value).length,clockCapacity(size.value));$('[data-clock-capacity]',host).textContent=shown<clockCities(value).length?tText('dashboard:clockCapacityHint',{shown,total:clockCities(value).length}):'';}catch(e){target.textContent=e.message;}
  }
  host.querySelectorAll('[data-clock-mode]').forEach(b=>b.onclick=()=>{$('[name=clock_mode]',host).value=b.dataset.clockMode;$('[name=clock_theme]',host).value=clockTheme(b.dataset.clockMode,$('[name=clock_theme]',host).value);faces();preview();});
  $('[data-clock-add]',host).onclick=()=>{try{storeCity();if(cities.length>=4)return;cities.push({timezone:'UTC',label:''});selected=cities.length-1;loadCity();cityList();preview();}catch(e){ctx.toast(e.message);}};
  $('[name=clock_world]',host).onchange=event=>{try{storeCity();if(!event.target.checked)selected=0;loadCity();cityList();preview();}catch(error){event.target.checked=!event.target.checked;ctx.toast(error.message);}};
  host.addEventListener('input',e=>{if(e.target.matches('[data-city-query]'))return;if(e.target.matches('input,select'))preview();});
  host.addEventListener('change',e=>{if(e.target.matches('input,select')&&!e.target.matches('[data-city-query]')){preview();cityList();}});
  loadCity();faces();cityList();preview();
  const observer=new MutationObserver(()=>{if(!modal.isConnected)dispose();});observer.observe(document.body,{childList:true});
  function dispose(){if(disposed)return;disposed=true;cleanup?.();searchCleanup?.();observer.disconnect();}
  return {read,dispose};
}
