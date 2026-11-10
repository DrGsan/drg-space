import {tText} from "./i18n.js";
import wallpaperCatalog from '../../app/wallpaper_catalog.json';
import retiredWallpapers from '../../app/wallpaper_retired.json';
import {initSeasonalDecorations,setSeasonalMode} from './seasonal.js';
export const appearanceDefaults=Object.freeze({theme:'ocean',mode:'system',day_start:'07:00',night_start:'22:00',style:'glass',wallpaper:'dunes',seasonal:'auto'});
const defaults=appearanceDefaults;
export const themes={ocean:tText("common:ocean"),sage:tText("common:forest"),amber:tText("common:sand"),lilac:tText("common:lavender"),rose:tText("common:rose"),slate:tText("common:graphite")};
// Artwork IDs are shared with the API and native client; names follow the
// current interface language without changing the account's saved selection.
export const wallpaperCategories=wallpaperCatalog.map(category=>({id:category.id,name:tText('server:'+category.name_key),items:category.items.map(item=>({id:item.id,name:tText('server:'+item.name_key)}))}));
export const wallpaperPresets=Object.fromEntries([...wallpaperCategories.flatMap(category=>category.items.map(item=>[item.id,item.name])),...retiredWallpapers.map(item=>[item.id,tText('server:'+item.name_key)])]);
export function wallpaperCategory(value){return wallpaperCategories.find(category=>category.items.some(item=>item.id===value))?.id||wallpaperCategories[0].id;}
export const wallpapers={...wallpaperPresets,dusk:tText("common:dusk"),mist:tText("common:mist"),none:tText("common:noImage")};
export function wallpaperUrl(value){
  if(Object.hasOwn(wallpaperPresets,value))return '/assets/wallpapers/'+value+'.webp';
  if(/^[a-f0-9]{32}$/.test(value||''))return '/api/media/'+value;
  return '';
}
export function wallpaperBackground(value){
  const url=wallpaperUrl(value);
  if(url)return `url("${url}")`;
  if(value==='dusk')return 'radial-gradient(ellipse at 15% 85%,#c18d73 0,transparent 55%),radial-gradient(ellipse at 85% 15%,#526e88 0,transparent 55%),linear-gradient(135deg,#d7c4b0,#354c62)';
  if(value==='mist')return 'radial-gradient(ellipse at 10% 20%,#e9e4d9 0,transparent 55%),radial-gradient(ellipse at 80% 75%,#8fa998 0,transparent 60%),linear-gradient(135deg,#d3ded2,#b5c8cb)';
  return 'none';
}
let preferences={...defaults},timer,previewWallpaper='';
const device=window.matchMedia('(prefers-color-scheme: dark)');
export function schemeFor(config,date=new Date(),systemDark=device.matches){
  if(config.mode==='system')return systemDark?'dark':'light';
  if(config.mode==='dark')return 'dark';
  if(config.mode!=='schedule')return 'light';
  const minutes=(value)=>{const [hour,minute]=value.split(':').map(Number);return hour*60+minute;};
  const day=minutes(config.day_start),night=minutes(config.night_start),now=date.getHours()*60+date.getMinutes();
  const light=day<night?now>=day&&now<night:now>=day||now<night;
  return light?'light':'dark';
}
function apply(){
  const scheme=schemeFor(preferences),root=document.documentElement;
  const changed=root.dataset.theme!==preferences.theme || root.dataset.scheme!==scheme || root.dataset.interface!==preferences.style || root.dataset.wallpaper!==preferences.wallpaper;
  if(root.dataset.theme!==preferences.theme)root.dataset.theme=preferences.theme;
  if(root.dataset.scheme!==scheme)root.dataset.scheme=scheme;
  if(root.dataset.interface!==preferences.style)root.dataset.interface=preferences.style;
  if(root.dataset.wallpaper!==preferences.wallpaper)root.dataset.wallpaper=preferences.wallpaper;
  const background=previewWallpaper?`url("${previewWallpaper}")`:wallpaperBackground(preferences.wallpaper);
  if(root.style.getPropertyValue('--app-wallpaper')!==background)root.style.setProperty('--app-wallpaper',background);
  if(root.style.colorScheme!==scheme)root.style.colorScheme=scheme;
  syncThemeColor();
  setSeasonalMode(preferences.seasonal);
  if(changed)window.dispatchEvent(new CustomEvent('drg-appearance'));
  clearTimeout(timer);timer=setTimeout(apply,60000-Date.now()%60000+10);
}
function syncThemeColor(){
  const themeColor=document.querySelector('meta[name="theme-color"]');
  const surface=document.querySelector('.auth-layout')||document.documentElement;
  if(themeColor)themeColor.content=getComputedStyle(surface).getPropertyValue('--bg').trim()||'#eff5f8';
}
export function setAppearance(input,cache=true,wallpaperPreview=''){
  previewWallpaper=!cache&&wallpaperPreview.startsWith('blob:')?wallpaperPreview:'';
  preferences={...defaults,...input};
  if(!themes[preferences.theme])preferences.theme=defaults.theme;
  if(!['classic','glass'].includes(preferences.style))preferences.style=defaults.style;
  if(!['auto','off'].includes(preferences.seasonal))preferences.seasonal=defaults.seasonal;
  if(!Object.hasOwn(wallpapers,preferences.wallpaper)&&!/^[a-f0-9]{32}$/.test(preferences.wallpaper||''))preferences.wallpaper=defaults.wallpaper;
  if(!['light','dark','schedule','system'].includes(preferences.mode))preferences.mode=defaults.mode;
  for(const field of ['day_start','night_start'])if(!/^([01]\d|2[0-3]):[0-5]\d$/.test(preferences[field]))preferences[field]=defaults[field];
  if(cache)try{localStorage.setItem('drg-appearance',JSON.stringify(preferences));}catch{}
  apply();
}
export function initAppearance(){
  initSeasonalDecorations();
  // A screen transition changes the public login canvas without changing the
  // account's saved workspace palette. Keep installed-app chrome in step.
  const app=document.getElementById('app');
  if(app)new MutationObserver(syncThemeColor).observe(app,{childList:true});
  // Older iOS Home Screen apps expose navigator.standalone even when the
  // display-mode media query has not updated after returning from Safari.
  const standalone=window.matchMedia('(display-mode: standalone)');
  const updateStandalone=()=>document.documentElement.classList.toggle('app-standalone',standalone.matches||navigator.standalone===true);
  updateStandalone();standalone.addEventListener('change',updateStandalone);
  window.addEventListener('pageshow',updateStandalone);
  try{setAppearance(JSON.parse(localStorage.getItem('drg-appearance')) || defaults,false);}catch{setAppearance(defaults,false);}
  device.addEventListener('change',apply);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)apply();});
  window.addEventListener('storage',event=>{if(event.key==='drg-appearance')try{setAppearance(JSON.parse(event.newValue),false);}catch{}});
}
