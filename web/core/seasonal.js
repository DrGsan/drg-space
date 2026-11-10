import themes from '../../app/seasonal_themes.json';
import {seasonalThemeForDate,nextSeasonalDateCheck,sortedSeasonalThemes} from './seasonal-calendar.js';
import {tText} from './i18n.js';
import {mobileApp} from './mobile.js';
import './seasonal.css';
import {holidaySymbol} from './seasonal-art.js';
import {syncSeasonalFrame} from './seasonal-frame.js';

export const seasonalThemes=sortedSeasonalThemes(themes);
const svg=body=>`<svg viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">${body}</svg>`;
const art={
  halloween:svg('<path d="M20 12c-4-6-16-2-16 10 0 10 8 14 16 12 8 2 16-2 16-12 0-12-12-16-16-10Z" fill="#e9983c"/><ellipse cx="20" cy="23" rx="9" ry="12" fill="#f9ae4f"/><path d="m19 12 1-8 5-2" stroke="#72945b" stroke-width="4" stroke-linecap="round"/><path d="m11 22 5-4 2 6-7-2Zm11 2 2-6 5 4-7 2Zm-8 4q6 6 12 0" fill="#754629"/>'),
  newyear:svg('<path d="M20 4v5m-3 1h6" stroke="#c5a45b" stroke-width="3" stroke-linecap="round"/><circle cx="20" cy="24" r="13" fill="#bd505f"/><path d="M8 23q12 9 24-1" stroke="#f0d296" stroke-width="3"/><path d="m14 15 1 2m-3 3 1 2" stroke="#fff1d9" stroke-width="2" stroke-linecap="round"/>'),
  valentine:svg('<path d="M20 34 6 20C-3 9 11-1 20 10 29-1 43 9 34 20Z" fill="#d8768f"/><path d="M10 11q-4 3-1 7" stroke="#fbe0e7" stroke-width="3" stroke-linecap="round"/>'),
  spring:svg('<path d="m13 36 14-31m-8 20-9-9m14 0 9-6" stroke="#69956f" stroke-width="2" stroke-linecap="round"/><path d="M15 33C4 33 2 23 3 21c10 0 14 4 12 12Zm7-13c11 1 14 8 13 10-10 0-14-3-13-10Z" fill="#86ae82"/><g fill="#e5b84e"><circle cx="22" cy="7" r="4"/><circle cx="29" cy="6" r="4"/><circle cx="24" cy="14" r="4"/><circle cx="13" cy="16" r="4"/><circle cx="7" cy="12" r="4"/></g>'),
  space:svg('<circle cx="20" cy="20" r="11" fill="#6260e8"/><path d="M10 16q9 6 19 3m-17 6 13 3" stroke="#bfd3fa" stroke-width="2"/><ellipse cx="20" cy="20" rx="21" ry="6" transform="rotate(-25 20 20)" stroke="#7954d6" stroke-width="3"/><circle cx="33" cy="5" r="2" fill="#e9bf52"/>')
};
const particles={
  halloween:svg('<path d="M20 20c-4-15-10-7-17-15 2 9-3 11-1 20 5-4 9-3 12 1l6 7 6-7c3-4 7-5 12-1 2-9-3-11-1-20-7 8-13 0-17 15Z" fill="#9d87b0"/>'),
  newyear:svg('<path d="M20 4v32M6 12l28 16M6 28l28-16" stroke="#aec5d8" stroke-width="3" stroke-linecap="round"/>'),
  valentine:art.valentine,
  spring:svg('<path d="M8 28C-2 3 33-2 32 9c0 10-13 22-24 19Z" fill="#e3b4c1"/>'),
  space:svg('<path d="m25 7-4 11L2 36l22-12 11-4-10-13Z" fill="#679fcd"/><circle cx="27" cy="13" r="6" fill="#e9bf52"/>')
};

// These are the existing workspace bars, not new rows above the content.
// Prefer only one bar on mobile when a workspace also keeps a hidden desktop bar.
const targets='.auth-header,.workspace .content>.topbar,.workspace #view-content>.section-heading,.tracker-chrome,.notes-navigation,.notes-shell .page-top,.tasks-nav-head,.tasks-toolbar,.chat-nav-head,.chat-sidebar-heading,.chat-header,.drive-workspace-header,.seasonal-preview-header';
let mode='auto',preview='',account='',current='',timer,observer,queued=false,started=false;
let previewControl,previewWorkspace;
const layers=new Map();
const dirtyLayouts=new Set();
let layoutObserver;

// Find a few clear horizontal intervals, rather than putting large artwork
// over a title or searching the header pixel by pixel. Bounds are read only
// when a header is mounted, resized, or its own contents change.
function placeArtwork(host,layer){
  const bounds=host.getBoundingClientRect(),compact=matchMedia('(max-width:760px)').matches;
  const blocked=[],seen=new Set(),styles=new Map(),rects=new Map();
  const styleOf=element=>{if(!styles.has(element))styles.set(element,getComputedStyle(element));return styles.get(element);};
  const rectOf=element=>{if(!rects.has(element))rects.set(element,element.getBoundingClientRect());return rects.get(element);};
  const closed=element=>{const details=element.closest('details:not([open])');return details&&!details.querySelector(':scope>summary')?.contains(element);};
  const add=(rect,padding=4)=>{if(rect.width>0&&rect.height>0&&rect.right>bounds.left&&rect.left<bounds.right&&rect.bottom>bounds.top&&rect.top<bounds.bottom)blocked.push({left:rect.left-bounds.left-padding,right:rect.right-bounds.left+padding,top:rect.top-bounds.top-padding,bottom:rect.bottom-bounds.top+padding});};
  const textRect=(rect,parent,root)=>{
    const result={left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom};
    for(let element=parent;element&&element!==root;element=element.parentElement){
      const style=styleOf(element);
      if(style.opacity==='0'||style.clipPath==='inset(50%)')return null;
      const clipsX=/(hidden|clip|auto|scroll)/.test(style.overflowX),clipsY=/(hidden|clip|auto|scroll)/.test(style.overflowY);
      if(clipsX||clipsY){const clip=rectOf(element);if(clipsX){result.left=Math.max(result.left,clip.left);result.right=Math.min(result.right,clip.right);}if(clipsY){result.top=Math.max(result.top,clip.top);result.bottom=Math.min(result.bottom,clip.bottom);}}
    }
    return {...result,width:result.right-result.left,height:result.bottom-result.top};
  };
  const read=root=>{
    for(const element of root.querySelectorAll('button,select,input,a,img,svg')){
      if(element.closest('.seasonal-decoration')||seen.has(element)||closed(element))continue;
      seen.add(element);const style=styleOf(element);
      if(!element.getClientRects().length||style.visibility==='hidden')continue;
      if(element.matches('button,a')){
        const clear=color=>color==='transparent'||/rgba\([^)]*,\s*0\s*\)/.test(color);
        const border=['Top','Right','Bottom','Left'].some(side=>parseFloat(style['border'+side+'Width'])>0&&style['border'+side+'Style']!=='none'&&!clear(style['border'+side+'Color']));
        if(clear(style.backgroundColor)&&style.backgroundImage==='none'&&style.boxShadow==='none'&&!border)continue;
      }
      add(rectOf(element));
    }
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
    while(walker.nextNode()){
      const node=walker.currentNode,parent=node.parentElement;
      if(!node.textContent.trim()||parent.closest('.seasonal-decoration,select,input,svg')||closed(parent)||styleOf(parent).visibility==='hidden')continue;
      const range=document.createRange();range.selectNodeContents(node);for(const rect of range.getClientRects()){const painted=textRect(rect,parent,root);if(painted)add(painted,2);}
    }
  };
  read(host);
  // Notes' scope selector is an overlay owned by the adjacent sidebar.
  if(host.matches('.notes-navigation'))for(const overlay of host.closest('.notes-shell')?.querySelectorAll('.pages-sidebar-head,.page-top-actions')||[])read(overlay);
  if(host.matches('.chat-sidebar-heading')){const bell=host.closest('.chat-sidebar')?.querySelector('.chat-nav-head');if(bell)read(bell);}
  const bulbs=[...layer.querySelectorAll('.seasonal-lights>i')];
  bulbs.forEach((bulb,index)=>{
    const left=bounds.width*.04+index*(bounds.width*.92-6)/Math.max(1,bulbs.length-1),top=3+index*.13;
    bulb.style.visibility=blocked.some(r=>r.left<left+6&&r.right>left&&r.top<top+8&&r.bottom>top)?'hidden':'visible';
  });
  const find=(sizes,preferred,ratio=1,edge=false)=>{
    for(const size of sizes){
      if(size>bounds.height-4)continue;
      const width=size*ratio;
      let best;
      const tops=edge?[-6,-8,-10,-12].filter(top=>size+top>=20):[...new Set([Math.max(2,(bounds.height-size)/2),2,Math.max(2,bounds.height-size-2)])];
      for(const top of tops){
        const intervals=blocked.filter(r=>r.top<top+size&&r.bottom>top).sort((a,b)=>a.left-b.left);
        let left=4;const gaps=[];
        for(const r of intervals){if(r.left>left)gaps.push([left,Math.min(bounds.width-4,r.left)]);left=Math.max(left,r.right);}
        if(left<bounds.width-4)gaps.push([left,bounds.width-4]);
        for(const [start,end] of gaps){
          if(end-start<width)continue;
          const x=Math.max(start,Math.min(end-width,bounds.width*preferred-width/2)),score=Math.abs(x+width/2-bounds.width*preferred)+Math.abs(top-(bounds.height-size)/2)*.3;
          if(!best||score<best.score)best={left:x,top,size,width,score};
        }
      }
      if(best)return best;
    }
  };
  const apply=(element,spot)=>{
    element.style.display=spot?'block':'none';
    if(!spot)return;
    Object.assign(element.style,{left:spot.left+'px',top:spot.top+'px',width:spot.width+'px',height:spot.size+'px'});
    blocked.push({left:spot.left-5,right:spot.left+spot.width+5,top:spot.top-5,bottom:spot.top+spot.size+5});
  };
  const maximum=Math.min(compact?36:60,bounds.height-8);
  const main=find([...new Set([maximum,...(compact?[32,28]:[52,44,36,28])])].filter(size=>size>=28&&size<=maximum),.55)||(compact?find([32,28],.55,1,true):null);
  apply(layer.querySelector('.seasonal-motif'),main);
  apply(layer.querySelector('.seasonal-frieze'),main?null:find([20,18,16,14],.5,4));
  const parts=[...layer.querySelectorAll('.seasonal-particle')];
  const count=compact?2:6;
  parts.forEach((part,index)=>apply(part,index<count?find(compact?[30,26,20]:[44,36,28],(index+1)/(count+1)):null));
  dirtyLayouts.delete(host);
}

function previewLayout(workspace,active){
  if(workspace.classList.contains('seasonal-preview-active')===active)return;
  const content=workspace.querySelector(':scope>.content');
  const scrollHost=window.matchMedia('(max-width:760px)').matches?workspace:content;
  // Desktop pages normally scroll the document. Temporarily give the content
  // its own viewport so the separate preview control never covers a document.
  const transfer=workspace.isConnected&&!mobileApp()&&scrollHost;
  const scroll=transfer?(active?window.scrollY:scrollHost.scrollTop):0;
  workspace.classList.toggle('seasonal-preview-active',active);
  if(transfer){
    if(active){scrollHost.scrollTop+=scroll;window.scrollTo(0,0);}
    else{scrollHost.scrollTop=0;window.scrollTo(0,scroll);}
  }
}

function syncPreviewControl(){
  const workspace=document.querySelector('#app>.workspace');
  if(previewWorkspace&&previewWorkspace!==workspace)previewLayout(previewWorkspace,false);
  previewWorkspace=workspace;
  if(!preview||!account||!workspace){
    previewControl?.remove();
    if(workspace)previewLayout(workspace,false);
    return;
  }
  if(!previewControl){
    previewControl=document.createElement('div');previewControl.className='seasonal-preview-control';
    const label=document.createElement('span');label.setAttribute('role','status');
    const button=document.createElement('button');button.type='button';button.dataset.seasonalEndPreview='';
    button.onclick=()=>setSeasonalPreview();
    previewControl.append(label,button);
  }
  const theme=themes.find(item=>item.id===preview);
  previewControl.setAttribute('aria-label',tText('appearance:seasonalPreview'));
  previewControl.firstElementChild.textContent=tText('appearance:seasonalPreviewActive',{name:tText('appearance:'+theme.name_key)});
  previewControl.lastElementChild.textContent=tText('appearance:seasonalEndPreview');
  if(previewControl.parentElement!==workspace)workspace.append(previewControl);
  previewLayout(workspace,true);
}

function artwork(theme){
  const decorations=Array.from({length:6},(_,i)=>`<i class="seasonal-particle seasonal-particle-${i}" style="--particle:${i}">${holidaySymbol(theme,i+1)}</i>`).join('');
  const lights=['newyear','halloween','valentine'].includes(theme)?'<span class="seasonal-lights">'+Array.from({length:14},(_,i)=>`<i style="--bulb:${i}"></i>`).join('')+'</span>':'';
  const leaves=Array.from({length:20},(_,i)=>`<i style="--leaf:${i}"></i>`).join('');
  const frieze=Array.from({length:4},(_,index)=>`<i>${(index%2?art:particles)[theme]??holidaySymbol(theme,index)}</i>`).join('');
  return `<span class="seasonal-edge">${leaves}</span>${lights}<span class="seasonal-motif">${holidaySymbol(theme)}</span><span class="seasonal-frieze">${frieze}</span>${decorations}`;
}

function remove(host,layer){
  const scope=host.matches('.notes-navigation')?host.closest('.notes-shell')?.querySelector('#notes-space'):null;
  if(scope)layoutObserver?.unobserve(scope);
  layer.remove();host.classList.remove('seasonal-host','seasonal-positioned');layers.delete(host);layoutObserver?.unobserve(host);dirtyLayouts.delete(host);
}

function refresh(){
  queued=false;
  syncPreviewControl();
  const publicLogin=document.querySelector('#app>.auth-layout');
  // Public screens use only the device calendar, never the previous account's
  // setting or preview. Workspace appearance takes over at the entry transition.
  current=publicLogin?seasonalThemeForDate(themes):preview||(mode==='auto'?seasonalThemeForDate(themes):'');
  syncSeasonalFrame(publicLogin||document.querySelector('#app>.workspace'),current,document.hidden);
  for(const [host,layer] of layers){
    if(!host.isConnected||!current||!host.matches(targets))remove(host,layer);
  }
  if(!current)return;
  for(const host of document.querySelectorAll(targets)){
    // Hidden bars do not receive animated DOM. Resize/visibility changes reconcile them.
    if(!host.getClientRects().length){if(layers.has(host))remove(host,layers.get(host));continue;}
    if(host.matches('.chat-sidebar-heading')&&!mobileApp()||host.matches('.chat-nav-head')&&mobileApp()){if(layers.has(host))remove(host,layers.get(host));continue;}
    if(mobileApp()&&host.matches('.section-heading')&&host.closest('.workspace')?.querySelector(':scope>.content>.topbar')?.getClientRects().length){if(layers.has(host))remove(host,layers.get(host));continue;}
    let layer=layers.get(host);
    if(!layer){
      layer=document.createElement('span');layer.className='seasonal-decoration';layer.setAttribute('aria-hidden','true');
      if(getComputedStyle(host).position==='static')host.classList.add('seasonal-positioned');
      host.classList.add('seasonal-host');host.prepend(layer);layers.set(host,layer);layoutObserver.observe(host);dirtyLayouts.add(host);
      const scope=host.matches('.notes-navigation')?host.closest('.notes-shell')?.querySelector('#notes-space'):null;
      if(scope)layoutObserver.observe(scope);
    }
    if(layer.dataset.seasonalTheme!==current){layer.dataset.seasonalTheme=current;layer.innerHTML=artwork(current);dirtyLayouts.add(host);}
    if(dirtyLayouts.has(host))placeArtwork(host,layer);
    layer.classList.toggle('seasonal-paused',document.hidden);
  }
}

function schedule(){
  if(!queued){queued=true;queueMicrotask(refresh);}
}

function dateChanged(){
  clearTimeout(timer);schedule();
  if(!document.hidden)timer=setTimeout(dateChanged,nextSeasonalDateCheck());
}

export function setSeasonalMode(value){mode=value==='off'?'off':'auto';if(started)dateChanged();}
export function getSeasonalPreview(){return preview;}
export function setSeasonalAccount(value){
  const next=typeof value==='string'?value:'';
  if(account===next)return;
  account=next;setSeasonalPreview();
}
export function setSeasonalPreview(value=''){
  preview=account&&themes.some(theme=>theme.id===value)?value:'';
  window.dispatchEvent(new CustomEvent('drg-seasonal-preview',{detail:preview}));
  if(started)schedule();
}
function layoutClasses(value){
  return (value||'').split(/\s+/).filter(name=>name&&!name.startsWith('seasonal-')).sort().join(' ');
}
export function initSeasonalDecorations(){
  if(started)return;started=true;
  layoutObserver=new ResizeObserver(entries=>{for(const {target} of entries){
    // The native picker can now resize without changing the adjacent bar.
    const host=layers.has(target)?target:target.matches('#notes-space')?target.closest('.notes-shell')?.querySelector('.notes-navigation'):null;
    if(host&&layers.has(host))dirtyLayouts.add(host);
  }schedule();});
  observer=new MutationObserver(records=>{
    // Mobile navigation can reveal an existing bar by changing only a class.
    // Ignore our own classes and descendants so reconciliation cannot loop.
    let changed=false;
    for(const record of records){
      const element=record.target.nodeType===1?record.target:record.target.parentElement;
      if(element?.closest('.seasonal-decoration,.seasonal-preview-control,.seasonal-shell-decoration,.seasonal-sidebar-scene'))continue;
      const rawHeader=element?.closest(targets)||((element?.closest('.pages-sidebar-head,.page-top-actions')||element?.matches('.pages-sidebar'))?element.closest('.notes-shell')?.querySelector('.notes-navigation'):null);
      const header=mobileApp()&&rawHeader?.matches('.chat-nav-head')?rawHeader.closest('.chat-sidebar')?.querySelector('.chat-sidebar-heading'):rawHeader;
      const relevant=record.type==='characterData'?Boolean(header):record.type==='attributes'?(record.attributeName==='class'
        ?layoutClasses(record.oldValue)!==layoutClasses(record.target.getAttribute('class'))
        :record.oldValue!==record.target.getAttribute(record.attributeName))
        :[...record.addedNodes,...record.removedNodes].some(node=>node.nodeType===1&&!node.matches?.('.seasonal-decoration,.seasonal-shell-decoration,.seasonal-sidebar-scene'));
      if(relevant){
        // Do not retain discarded workspace DOM while holiday decoration is off.
        changed=true;if(header&&layers.has(header))dirtyLayouts.add(header);
        // A mode class on the containing workspace can reveal different
        // controls without changing the header's own size (notes list/page).
        if(record.type==='attributes')for(const host of layers.keys())if(element.contains(host))dirtyLayouts.add(host);
      }
    }
    if(changed)schedule();
  });
  observer.observe(document.getElementById('app'),{childList:true,characterData:true,subtree:true,attributes:true,attributeOldValue:true,attributeFilter:['class','hidden','open']});
  const resized=()=>{for(const host of layers.keys())dirtyLayouts.add(host);schedule();};
  window.addEventListener('resize',resized);
  document.fonts.ready.then(resized);
  document.addEventListener('visibilitychange',dateChanged);
  window.addEventListener('pageshow',dateChanged);
  dateChanged();
}
