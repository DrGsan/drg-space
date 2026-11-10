import {tText,tHtml,locale} from "../../../core/i18n.js";
import { esc } from "../../../core/ui.js";
import { widgetSize } from '../sizes.js';

const weatherCode = code => code < 0 || !Number.isFinite(code) ? tText("dashboard:noDescription")
  : code === 68 || code === 69 ? tText("dashboard:snowy")
  : code === 0 ? tText("dashboard:clear") : code <= 3 ? tText("dashboard:cloudy") : code <= 48 ? tText("dashboard:mist")
  : code <= 67 ? tText("dashboard:rain") : code <= 77 ? tText("dashboard:snow") : code <= 82 ? tText("dashboard:heavyRain")
  : code <= 86 ? tText("dashboard:snowfall") : tText("dashboard:thunderstorm");
const weatherIcon = code => !Number.isFinite(code) || code < 0 ? '—'
  : code === 0 ? '☀' : code <= 3 ? '☁' : code <= 48 ? '≋'
  : code === 68 || code === 69 ? '☂' : code <= 67 || (code >= 80 && code <= 82) ? '☂'
  : code <= 86 ? '❄' : 'ϟ';
// The current condition scales independently from the small forecast glyphs.
const weatherSymbols = {
  '☀': '<circle cx="12" cy="12" r="4" fill="currentColor" stroke="none"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
  '☁': '<path d="M6.5 19a5.5 5.5 0 0 1-1-10.9 6.5 6.5 0 0 1 12-1.6A6.3 6.3 0 0 1 18 19Z" fill="currentColor" stroke="none"/>',
  '≋': '<path d="M3 7h18M5 12h14M3 17h18"/>',
  '☂': '<path d="M2 12a10 10 0 0 1 20 0c-1.4-2-3.6-2-5 0-1.4-2-3.6-2-5 0-1.4-2-3.6-2-5 0-1.4-2-3.6-2-5 0Z" fill="currentColor" stroke="none"/><path d="M12 2v1m0 9v8a2 2 0 0 0 4 0"/>',
  '❄': '<path d="M12 2v20M3.3 7l17.4 10M3.3 17 20.7 7M9 4l3 3 3-3M9 20l3-3 3 3M3.6 10.6l4.1-1.1-1.1-4.1m14.8 8-4.1 1.1 1.1 4.1M6.6 18.6l1.1-4.1-4.1-1.1m14.8-8-1.1 4.1 4.1 1.1"/>',
  'ϟ': '<path d="m14 2-9 12h6l-1 8 9-12h-6Z" fill="currentColor" stroke="none"/>',
  '—': '<path d="M5 12h14"/>'
};
const weatherSymbol = code => `<span class="weather-symbol" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" focusable="false">${weatherSymbols[weatherIcon(code)]}</svg></span>`;
const temperature = value => Number.isFinite(value) ? Math.round(value) + '°' : '—';
const amount = value => Number.isFinite(value) ? value.toLocaleString(locale(), {maximumFractionDigits:1}) : '—';
const metric = (name,value,title='') => `<div><dt${title?` title="${esc(title)}"`:''}>${name}</dt><dd>${value}</dd></div>`;
const direction = value => [tText("dashboard:n"),tText("dashboard:nw"),tText("dashboard:w"),tText("dashboard:sw"),tText("dashboard:s2"),tText("dashboard:se"),tText("dashboard:e"),tText("dashboard:ne")][Math.round(value / 45) % 8];
const rain = point => Number.isFinite(point.precipitation_mm) && Number.isFinite(point.precipitation_hours)
  ? tText("dashboard:mmHours", {value0:(amount(point.precipitation_mm)), value1:(point.precipitation_hours)}) : '';
function weatherTrend(points){
  if(points.length<2)return '';
  const values=points.map(p=>p.temperature_2m),low=Math.min(...values),high=Math.max(...values);
  const line=values.map((v,i)=>`${8+i*224/(values.length-1)},${high===low?30:54-(v-low)*46/(high-low)}`).join(' ');
  return `<div class="weather-trend"><span>${tHtml("dashboard:temperatureByTheHour")}</span><svg viewBox="0 0 240 62" preserveAspectRatio="none" role="img" aria-label="${tHtml("dashboard:temperatureChangeFromTo", {value0:(temperature(values[0])), value1:(temperature(values.at(-1)))})}"><line x1="8" y1="54" x2="232" y2="54"/><polyline points="${line}"/></svg><div><span>${temperature(low)}…${temperature(high)}</span><span>${tHtml("dashboard:addHours", {value0:(Math.round((points.at(-1).time-points[0].time)/3600))})}</span></div></div>`;
}

export function weatherMarkup(w, size='1x1') {
  size=widgetSize(size);
  const wide=size==='2x1'||size==='2x2',tall=size==='1x2'||size==='2x2';
  const current=w.current,checkedAt=w.checked_at||w.updated;
  const checked=checkedAt?new Date(checkedAt*1000).toLocaleTimeString(locale(),{hour:'2-digit',minute:'2-digit'}):'';
  const period=w.forecast_range==='next_24h'?tText("dashboard:next24Hours"):tText("dashboard:today");
  const periodLabel=`<span title="${period}"><span class="sr-only">${period}</span><span class="weather-range-full" aria-hidden="true">${period}</span><span class="weather-range-short" aria-hidden="true">${w.forecast_range==='next_24h'?tText("dashboard:label24Hours2"):period}</span></span>`;
  const low=temperature(w.daily?.temperature_2m_min?.[0]),high=temperature(w.daily?.temperature_2m_max?.[0]);
  const feels=Number.isFinite(current.apparent_temperature)?(" "+tText("dashboard:feelsLike", {value0:(temperature(current.apparent_temperature))})+""):'';
  const windFrom=Number.isFinite(current.wind_direction)?direction(current.wind_direction):'';
  const metrics=[metric(tText("dashboard:wind"),tText("dashboard:kmH", {value0:(Math.round(current.wind_speed_10m)), value1:(tall&&windFrom?' · '+windFrom:'')}))];
  if(tall){
    if(Number.isFinite(current.relative_humidity))metrics.push(metric(tText("dashboard:humidity"),`${Math.round(current.relative_humidity)}%`));
    if(Number.isFinite(current.pressure_hpa))metrics.push(metric(tText("dashboard:pressure"),tText("dashboard:gpa", {value0:(Math.round(current.pressure_hpa))}),tText("dashboard:seaLevelPressure")));
    if(rain(current))metrics.push(metric(tText("dashboard:precipitation"),rain(current)));
  }
  const hours=(w.hourly||[]).filter(p=>Number.isFinite(p.time)&&Number.isFinite(p.temperature_2m)).slice(0,6);
  const now=`<section class="weather-now" aria-label="${tHtml("dashboard:currentForecast")}"><p class="weather-city" title="${esc(w.city)}">${esc(w.city)}</p><div class="weather-current"><strong class="weather-temperature">${temperature(current.temperature_2m)}</strong><div class="weather-summary">${weatherSymbol(current.weather_code)}<p class="weather-condition">${weatherCode(current.weather_code)}${feels}</p></div></div><div class="weather-range">${periodLabel}<strong>↓ ${low} <span>↑ ${high}</span></strong></div><dl class="weather-metrics">${metrics.join('')}</dl>${size==='2x2'?weatherTrend(hours):''}</section>`;
  const hourly=wide?`<section class="weather-hourly" aria-label="${tHtml("dashboard:hourlyForecast")}"><h3>${tHtml("dashboard:nextFewHours")}</h3>${hours.length?`<div class="weather-hours">${hours.map(p=>{
    const offset=Math.max(0,Math.round((p.time-w.forecast_time)/3600));
    return `<article class="weather-hour"><span class="weather-step">${offset?tText("dashboard:hours", {value0:(offset)}):tText("dashboard:now")}</span><strong><span aria-hidden="true">${weatherIcon(p.weather_code)}</span> ${temperature(p.temperature_2m)}</strong>${rain(p)?`<small>${esc(rain(p))}</small>`:''}${size==='2x2'&&Number.isFinite(p.wind_speed_10m)?`<small>${tHtml("dashboard:kmH2", {value0:(Math.round(p.wind_speed_10m))})}</small>`:''}</article>`;
  }).join('')}</div>`:`<p class="weather-unavailable">${tHtml("dashboard:hourlyForecastUnavailable")}</p>`}</section>`:'';
  const periods=(w.forecast_periods||[]).filter(p=>Number.isFinite(p.start)&&Number.isFinite(p.end)).slice(0,size==='2x2'?5:3);
  const days=tall?`<section class="weather-periods" aria-label="${tHtml("dashboard:forecastForTheNextDay")}"><h3>${size==='2x2'?tText("dashboard:forecastByDays"):tText("dashboard:forecastForTheNextDay")}</h3>${periods.length?`<div class="weather-period-list">${periods.map((p,i)=>`<article class="weather-period"><div><strong>${i===0&&size!=='2x2'?tText("dashboard:next24Hours2"):tText("dashboard:hours2", {value0:(i*24), value1:((i+1)*24)})}</strong><span>${size==='2x2'&&Number.isFinite(p.wind_speed_10m_max)?tText("dashboard:windUpToKmH", {value0:(Math.round(p.wind_speed_10m_max))}):weatherCode(p.weather_code)}</span></div><span class="weather-period-icon" role="img" aria-label="${weatherCode(p.weather_code)}" title="${weatherCode(p.weather_code)}">${weatherIcon(p.weather_code)}</span><b>↓ ${temperature(p.temperature_2m_min)}<br>↑ ${temperature(p.temperature_2m_max)}</b></article>`).join('')}</div>`:`<p class="weather-unavailable">${tHtml("dashboard:forecastForTheNextDayUnavailable")}</p>`}</section>`:'';
  const freshness=w.stale?tText("dashboard:latestAvailableDataServiceCurrentlyUnavailable"):tText("dashboard:forecast", {value0:(w.forecast_time?(" "+tText("dashboard:for", {value0:(new Date(w.forecast_time*1000).toLocaleString(locale()))})+""):'')});
  const footer=`<footer class="weather-source"><span><a href="https://api.met.no/weatherapi/locationforecast/2.0/documentation" target="_blank" rel="noopener noreferrer">MET Norway</a> · <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noopener noreferrer">CC BY 4.0</a></span><span class="weather-freshness" title="${esc(freshness+(checked?(" "+tText("dashboard:verified", {value0:(checked)})+""):''))}">${w.stale?(""+tText("dashboard:cache")+" "):''}${checked||tText("dashboard:forecast2")}</span></footer>`;
  return `<div class="weather-view weather-view-${size}${w.stale?' is-stale':''}">${now}${hourly}${days}${footer}</div>`;
}

export async function mountWeather(host, ctx) {
  const {api}=ctx,$=s=>host.querySelector(s);
  host.innerHTML=`<div class="weather-status"><p class="muted">${tHtml("dashboard:loadingForecast")}</p></div>`;
  let disposed=false,loading=false,timer;
  const load=async()=>{
    if(disposed||loading||document.hidden)return;
    loading=true;clearTimeout(timer);
    const retry=$('[data-retry-weather]');if(retry)retry.disabled=true;
    try {
      const w=await api('/widgets/'+ctx.instance.id+'/weather');
      if(disposed||!host.isConnected)return;
      if(!w.configured){
        host.innerHTML=`<div class="weather-status weather-empty">${weatherSymbol(0)}<div class="weather-empty-actions"><p>${tHtml("dashboard:selectACityForThisWidget")}</p><button data-set-city class="secondary compact">${tHtml("dashboard:selectCity")}</button></div></div>`;
        $('[data-set-city]').onclick=()=>ctx.configure();return;
      }
      host.innerHTML=weatherMarkup(w,ctx.instance.size);
      timer=setTimeout(load,w.stale?60000:600000);
    } catch(e) {
      if(!disposed&&host.isConnected){
        host.innerHTML=`<div class="weather-status"><p class="weather-status-message muted" title="${esc(e.message)}">${esc(e.message)}</p><button class="secondary compact" data-retry-weather>${tHtml("dashboard:tryLoadingAgain")}</button><button class="text-link" data-set-city>${tHtml("dashboard:changeCity")}</button></div>`;
        $('[data-retry-weather]').onclick=load;$('[data-set-city]').onclick=ctx.configure;
        if(![401,403,404].includes(e.status))timer=setTimeout(load,60000);
      }
    } finally {loading=false;}
  };
  const online=()=>load(),visible=()=>{if(!document.hidden)load();};
  window.addEventListener('online',online);document.addEventListener('visibilitychange',visible);load();
  return ()=>{disposed=true;clearTimeout(timer);window.removeEventListener('online',online);document.removeEventListener('visibilitychange',visible);};
}
