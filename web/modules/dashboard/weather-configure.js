import {tText,tHtml} from "../../core/i18n.js";
import { $, esc } from '../../core/ui.js';

// Photon limits are shared by the server. Keep searches sequential even when a
// dialog is closed and another opens while its previous request is finishing.
let searchTail=Promise.resolve();
function sequentialSearch(search){
  const result=searchTail.then(search);
  searchTail=result.catch(()=>{});
  return result;
}

export function configureWeather(host,instance,modal,ctx,{onChoose,requireTimezone=false}={}){
  const config={city:'',latitude:null,longitude:null,...instance.config};
  const statusId='city-search-status-'+instance.id;
  host.innerHTML=`<p data-selected-city class="muted">${esc(config.city || tText("dashboard:cityNotSelected"))}</p><label>${tHtml("dashboard:searchForCity")}<input data-city-query placeholder="${tHtml("dashboard:enterCity")}" maxlength="100" autocomplete="off" aria-describedby="${esc(statusId)}"></label><p id="${esc(statusId)}" data-city-status class="hint" role="status" aria-live="polite" aria-atomic="true">${tHtml("dashboard:enterAtLeast2CharactersCitiesWillAppear")}</p><div data-city-results role="group" aria-label="${tHtml("dashboard:foundCities")}" aria-busy="false"></div><p class="hint">${tHtml("dashboard:search")} <a href="https://open-meteo.com/en/docs/geocoding-api" target="_blank" rel="noopener noreferrer">Open-Meteo</a> · <a href="https://www.geonames.org/" target="_blank" rel="noopener noreferrer">GeoNames</a> · <a href="https://photon.komoot.io/" target="_blank" rel="noopener noreferrer">Photon</a> · <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">© OpenStreetMap</a></p>`;
  const input=$('[data-city-query]',host),results=$('[data-city-results]',host),status=$('[data-city-status]',host);
  let sequence=0,timer=null,inFlight=false,requestSequence=null,requestQuery='',ready=false,composing=false,disposed=false;
  const live=()=>!disposed && modal.isConnected;
  function invalidate(){
    sequence++;
    clearTimeout(timer);timer=null;ready=false;
    results.replaceChildren();results.setAttribute('aria-busy','false');
  }
  function choose(city){
    if(!live())return;
    invalidate();
    Object.assign(config,{city:city.name,latitude:city.latitude,longitude:city.longitude});
    onChoose?.(city);
    $('[data-selected-city]',host).textContent=tText("dashboard:citySelected", {value0:(city.name)});
    input.value=city.name;status.textContent=tText("dashboard:citySelectedYouCanSaveTheWidget");
    input.focus();
  }
  async function search(){
    if(inFlight || !ready || !live())return;
    const current=sequence,query=input.value.trim();
    ready=false;inFlight=true;requestSequence=current;requestQuery=query;
    try{
      const response=await sequentialSearch(()=>{
        if(!live() || current!==sequence)return null;
        return ctx.api('/weather/search?q='+encodeURIComponent(query));
      });
      if(!live() || current!==sequence || response===null)return;
      const cities=requireTimezone?response.filter(city=>city.timezone):response;
      results.innerHTML=cities.map((city,i)=>`<button type="button" class="city-result" data-city="${i}"><strong>${esc(city.name)}</strong><span>${esc([city.region,city.country].filter(Boolean).join(', '))}</span></button>`).join('');
      results.querySelectorAll('[data-city]').forEach(button=>button.onclick=()=>choose(cities[Number(button.dataset.city)]));
      status.textContent=cities.length?tText("dashboard:citiesFoundSelectTheAppropriateOne", {value0:(cities.length)}):tText("dashboard:nothingFoundTryAnotherName");
    }catch(error){
      if(live() && current===sequence)status.textContent=error.message || tText("dashboard:couldNotFindCitiesTryAgain");
    }finally{
      inFlight=false;
      if(live() && current===sequence)results.setAttribute('aria-busy','false');
      // While a request is running, only the most recent debounced query waits.
      if(ready && live())search();
    }
  }
  function schedule(immediate=false){
    invalidate();
    if(!live())return;
    if(composing || input.value.trim().length<2){
      status.textContent=tText("dashboard:enterAtLeast2CharactersCitiesWillAppear");
      return;
    }
    status.textContent=tText("dashboard:searchingForCities");results.setAttribute('aria-busy','true');
    if(immediate){ready=true;search();}
    else timer=setTimeout(()=>{timer=null;ready=true;search();},400);
  }
  input.oninput=()=>schedule();
  input.oncompositionstart=()=>{composing=true;schedule();};
  input.oncompositionend=()=>{composing=false;schedule();};
  input.onkeydown=event=>{
    if(event.key!=='Enter')return;
    if(event.isComposing || composing)return;
    event.preventDefault();
    if(inFlight && requestSequence===sequence && requestQuery===input.value.trim())return;
    schedule(true);
  };
  const observer=new MutationObserver(()=>{if(!modal.isConnected)dispose();});
  observer.observe(document.body,{childList:true});
  modal.addEventListener('beforeclose',dispose);
  function dispose(){
    if(disposed)return;
    disposed=true;invalidate();observer.disconnect();modal.removeEventListener('beforeclose',dispose);
  }
  return {read:()=>config,dispose};
}
