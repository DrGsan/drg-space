import {tHtml,tText} from '../../../core/i18n.js';
import {esc} from '../../../core/ui.js';
import {clockDefaults,clockTimezone,clockCity,clockParts,clockTime,clockOffset,clockDate,clockAngles,clockPeriod,clockTheme,clockCities,clockCapacity} from './clock-format.js';
import {widgetSize} from '../sizes.js';
import {subscribeClock} from './clock-ticker.js';

export function clockFace(theme,seconds){
  const marks=Array.from({length:60},(_,i)=>{
    if(['minimal','orbit'].includes(theme)&&i%5)return '';
    if(theme==='orbit')return `<circle class="clock-orbit-mark" cx="120" cy="19" r="${i%15===0?3:1.5}" transform="rotate(${i*6} 120 120)"/>`;
    return `<line class="clock-tick ${i%5===0?'major':''}" x1="120" y1="${i%5===0?23:19}" x2="120" y2="14" transform="rotate(${i*6} 120 120)"/>`;
  }).join('');
  const numbers=['classic','roman','bold'].includes(theme)?Array.from({length:12},(_,i)=>i+1):theme==='minimal'?[3,6,9,12]:[];
  const roman=['I','II','III','IV','V','VI','VII','VIII','IX','X','XI','XII'];
  const numerals=numbers.map(n=>{const a=n*Math.PI/6;return `<text class="clock-number" x="${120+Math.sin(a)*78}" y="${120-Math.cos(a)*78}" text-anchor="middle" dominant-baseline="central">${theme==='roman'?roman[n-1]:n}</text>`;}).join('');
  return `<svg class="clock-face" viewBox="0 0 240 240" role="img" aria-label="${tHtml('dashboard:analogClock')}"><circle class="clock-dial" cx="120" cy="120" r="114"/>${theme==='orbit'?'<circle class="clock-orbit" cx="120" cy="120" r="81"/>':''}${marks}${numerals}<g data-hand="hour"><line class="clock-hour-hand" x1="120" y1="127" x2="120" y2="63"/></g><g data-hand="minute"><line class="clock-minute-hand" x1="120" y1="129" x2="120" y2="37"/></g>${seconds?'<g data-hand="second"><line class="clock-second-hand" x1="120" y1="143" x2="120" y2="28"/></g>':''}<circle class="clock-pivot" cx="120" cy="120" r="5"/></svg>`;
}
const segmentMap=['abcedf','bc','abged','abgcd','fgbc','afgcd','afgecd','abc','abcdefg','abfgcd'];
const segmentLines={a:[5,3,23,3],b:[26,6,26,22],c:[26,28,26,44],d:[5,47,23,47],e:[2,28,2,44],f:[2,6,2,22],g:[5,25,23,25]};
function segment(value){return `<svg class="clock-segment-number" viewBox="0 0 28 50" aria-hidden="true">${Object.entries(segmentLines).map(([key,coords])=>`<line x1="${coords[0]}" y1="${coords[1]}" x2="${coords[2]}" y2="${coords[3]}" class="${segmentMap[Number(value)].includes(key)?'on':'off'}"/>`).join('')}</svg>`;}
export function clockDigits(theme,p,config){
  const hour=String(config.hour_format==='12h'?(p.hour%12||12):p.hour).padStart(2,'0'),minute=String(p.minute).padStart(2,'0');
  const part=(value,kind)=>`<span class="clock-${kind}">${theme==='segments'?[...value].map(segment).join(''):value}</span>`;
  return `${part(hour,'hours')}<span class="clock-colon">:</span>${part(minute,'minutes')}${config.show_seconds?`<span class="clock-seconds">${String(p.second).padStart(2,'0')}</span>`:''}${config.hour_format==='12h'?`<span class="clock-meridiem">${p.hour<12?'AM':'PM'}</span>`:''}`;
}
export function mountClock(host,ctx){
  const config={...clockDefaults,...ctx.instance.config},theme=clockTheme(config.mode,config.theme),size=widgetSize(ctx.instance.size);
  const cities=clockCities(config).slice(0,clockCapacity(size));
  let unsubscribe,disposed=false,visible=true;
  const reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)');
  try{for(const city of cities)clockParts(new Date(),city.timezone);}catch{
    host.innerHTML=`<p class="error">${tHtml('dashboard:thisRegionIsNotSupportedInYourBrowser')}</p>`;return;
  }
  host.innerHTML=`<div class="clock-collection" data-clock-size="${esc(size)}" data-count="${cities.length}">${cities.map(city=>{
    const name=city.label||clockCity(city.timezone),details=`${city.label||config.cities.length?`<div class="clock-label">${esc(name)}</div>`:''}${config.show_timezone?`<div class="clock-region"><span>${esc(city.label||config.cities.length?'':name)}</span><span class="clock-zone-offset"></span>${config.mode==='digital'?'<span class="clock-zone-period"></span>':''}</div>`:''}${config.show_date?'<div class="clock-date"></div>':''}`;
    return `<div class="clock-widget clock-${config.mode} clock-theme-${theme}" data-clock-zone="${esc(city.timezone)}" aria-live="off">${config.mode==='analog'?`<div class="clock-face-area">${clockFace(theme,config.show_seconds)}</div>`:'<time class="clock-digits"></time>'}${details?`<div class="clock-details">${details}</div>`:''}</div>`;
  }).join('')}</div>`;
  const clocks=Array.from(host.querySelectorAll('.clock-widget')).map((widget,i)=>({widget,city:cities[i],svg:widget.querySelector('.clock-face'),digits:widget.querySelector('.clock-digits'),date:widget.querySelector('.clock-date'),offset:widget.querySelector('.clock-zone-offset'),region:widget.querySelector('.clock-region>span'),label:widget.querySelector('.clock-label'),period:widget.querySelector('.clock-zone-period'),hands:Array.from(widget.querySelectorAll('[data-hand]')),second:widget.querySelector('[data-hand=second]'),animation:null}));
  const setText=(node,value)=>{if(node&&node.textContent!==value)node.textContent=value;};
  function stopSweep(){for(const clock of clocks){clock.animation?.cancel();clock.animation=null;clock.second?.style.removeProperty('transform-origin');}}
  function draw(now){
    if(disposed||!host.isConnected)return;
    for(const clock of clocks){
      const resolved={...config,...clock.city,timezone:clockTimezone(clock.city.timezone)},p=clockParts(now,resolved.timezone),time=clockTime(p,config),city=clock.city.label||clockCity(resolved.timezone);
      setText(clock.region,clock.city.label||config.cities.length?'':city);
      setText(clock.label,city);
      clock.widget.dataset.clockPeriod=clockPeriod(p);
      if(clock.svg){const a=clockAngles(p,config.show_seconds);for(const hand of clock.hands)hand.setAttribute('transform',`rotate(${a[hand.dataset.hand]} 120 120)`);clock.svg.setAttribute('aria-label',`${city} · ${time}`);}
      if(clock.second&&!reducedMotion.matches){
        if(!clock.animation){clock.second.style.transformOrigin='120px 120px';clock.animation=clock.second.animate([{transform:'rotate(0deg)'},{transform:'rotate(360deg)'}],{duration:60000,iterations:Infinity,easing:'linear'});}
        clock.animation.currentTime=p.second*1000+now.getMilliseconds();
      }
      if(clock.digits){
        // Keep hour/minute nodes stable while seconds change.
        if(clock.minute!==`${p.hour}:${p.minute}`){clock.digits.innerHTML=clockDigits(theme,p,config);clock.minute=`${p.hour}:${p.minute}`;}
        else setText(clock.digits.querySelector('.clock-seconds'),String(p.second).padStart(2,'0'));
        clock.digits.dateTime=now.toISOString();clock.digits.setAttribute('aria-label',`${city} · ${time}`);
      }
      if(clock.offset)setText(clock.offset,clockOffset(now,resolved.timezone));
      if(clock.period)setText(clock.period,'· '+tText(clockPeriod(p)==='day'?'dashboard:clockDay':'dashboard:clockNight'));
      if(clock.date)setText(clock.date,clockDate(now,resolved));
    }
  }
  function schedule(){
    unsubscribe?.();unsubscribe=undefined;
    if(disposed||document.hidden||!visible||!host.isConnected){stopSweep();return;}
    if(reducedMotion.matches)stopSweep();
    unsubscribe=subscribeClock(draw,config.show_seconds);
  }
  const observer=new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;schedule();});observer.observe(host);
  schedule();document.addEventListener('visibilitychange',schedule);reducedMotion.addEventListener('change',schedule);
  return ()=>{disposed=true;unsubscribe?.();stopSweep();observer.disconnect();document.removeEventListener('visibilitychange',schedule);reducedMotion.removeEventListener('change',schedule);};
}
