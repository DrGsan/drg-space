import {holidaySymbol,holidayScene} from './seasonal-art.js';
import themes from '../../app/seasonal_themes.json';
import {seasonalParticles} from './seasonal-particles.js';

let workspace,frame,canvas,scene,sidebar,resize,surfaceResize,scheduled=0,needsLayout=false;
let mask,maskBase,maskHoles,surfaces=[];
let serial=0;
const svgNS='http://www.w3.org/2000/svg';
// Glass surfaces need real cutouts; the SVG mask remains mounted while scrolling.
const readingSurfaces='.auth-intro,.auth-card,.auth-footer,.widget,.panel,.preference-row,.page-heading,.section-heading,.topbar,.tracker-chrome,.tracker-board-toolbar,.tracker-mobile-columns,.tracker-column,.tracker-surface,.tracker-list,.tracker-table,.tracker-hierarchy,.notes-shell,.chat-shell,.tasks-shell,.drive-workspace,.social-view,.expense-layout,.mobile-settings-sections,.mobile-account,.mobile-section-links,.mobile-signout,button,input,select,textarea,label,h1,h2,h3,p,table,[contenteditable],.empty-state';
const rect=element=>element?.getClientRects().length?element.getBoundingClientRect():null;
function attribute(node,name,value){const next=String(value);if(node.getAttribute(name)!==next)node.setAttribute(name,next);}
function layout(){
  scheduled=0;if(!workspace?.isConnected||!frame)return;
  const full=needsLayout;needsLayout=false;
  const width=innerWidth,height=innerHeight;
  if(full){
    const content=workspace.querySelector(':scope>.content,:scope>.auth-main'),bounds=rect(content);
    const candidates=[...(content?.querySelectorAll(readingSurfaces)||[])].filter(e=>!e.parentElement.closest(readingSurfaces));
    const auth=workspace.matches('.auth-layout');
    const elements=[...(sidebar?[{element:sidebar}]:[]),...(candidates.length>100?[content]:candidates).map(element=>({element})),...Array.from(workspace.querySelectorAll(auth?':scope>.auth-header .brand-mark,:scope>.auth-header .brand-name,:scope>.auth-header .public-language select,:scope>.auth-footer':'.mobile-app-tabs,.seasonal-preview-control'),element=>({element,footer:element.matches('.mobile-app-tabs,.seasonal-preview-control,.auth-footer')}))];
    // Reconcile only on content/layout changes. No selectors or DOM replacement
    // run in the scroll path, and unchanged mask coordinates cause no writes.
    if(elements.length!==surfaces.length||elements.some((s,i)=>s.element!==surfaces[i].element)){
      surfaces=elements.map(s=>({...s,node:document.createElementNS(svgNS,'rect')}));
      for(const s of surfaces)s.node.setAttribute('fill','black');
      maskHoles.replaceChildren(...surfaces.map(s=>s.node));
      surfaceResize.disconnect();
      for(const s of surfaces)surfaceResize.observe(s.element);
    }
    // Mask the visible surface itself. An inflated square leaves an invisible
    // gap around cards and slices ornaments before they reach the card edge.
    for(const s of surfaces){const style=getComputedStyle(s.element);s.radius=Math.min(...['TopLeft','TopRight','BottomRight','BottomLeft'].map(corner=>parseFloat(style['border'+corner+'Radius'])||0));}
    attribute(mask,'width',width);attribute(mask,'height',height);
    attribute(maskBase,'width',width);attribute(maskBase,'height',height);
    for(const [i,particle] of [...canvas.querySelectorAll('.seasonal-ambient')].entries()){
      const kind=particle.dataset.particleKind,y=kind==='stars'?height*(i*17%100)/100:kind==='hearts'||kind==='notes'?height+20:-20;
      attribute(particle.parentElement,'transform',`translate(${width*(i*37%100)/100} ${y})`);
    }
    frame.dataset.left=bounds?.left||0;
    const compact=width<=760,top=compact?72:100,bottom=compact?150:76,strip=compact?48:68;
    const total=compact?136:176,gap=(height-top-bottom-total)/6;
    for(const side of ['left','right']){
      let y=top+gap;
      for(const [i,piece] of [...canvas.querySelectorAll('.seasonal-frame-'+side+'>g')].entries()){
        const size=auth&&!compact?(i===1?80:104):compact?(i===1?40:48):(i===1?48:64);
        const x=side==='left'?Number(frame.dataset.left)-(compact?30:32)+(strip-size)/2:width-strip+(compact?30:34)+(strip-size)/2;
        attribute(piece,'transform',`translate(${x} ${y})`);y+=size+2*gap;
      }
    }
    if(sidebar&&scene){
      const side=rect(sidebar),nav=rect(sidebar.querySelector('nav')),bottom=rect(sidebar.querySelector('.sidebar-bottom'));
      const available=nav&&bottom?bottom.top-nav.bottom-20:0;
      scene.hidden=!side||available<65;
      if(!scene.hidden){const h=Math.min(150,available);Object.assign(scene.style,{top:(bottom.top-side.top-h-8)+'px',height:h+'px'});}
    }
  }
  // Read cached surfaces first, then update the existing SVG rectangles. This
  // avoids interleaved forced layouts and data-URL image decoding during scroll.
  const measured=surfaces.map(s=>({...s,bounds:rect(s.element)}));
  let sceneBottom=height-10;
  for(const {node,radius,footer,bounds:b} of measured){
    const box=b?{x:b.left,y:b.top,width:b.width,height:b.height,rx:radius,ry:radius}:{x:0,y:0,width:0,height:0,rx:0,ry:0};
    for(const [name,value] of Object.entries(box))attribute(node,name,value);
    if(footer&&b)sceneBottom=Math.min(sceneBottom,b.top-8);
  }
  const compact=width<=760,ground=canvas.lastElementChild;
  attribute(ground,'transform',`translate(${compact?width-170:Number(frame.dataset.left)+30} ${sceneBottom-(compact?105:140)})`);
}
function schedule(full=true){needsLayout=needsLayout||full;if(!scheduled&&frame)scheduled=requestAnimationFrame(layout);}
const scrolled=()=>schedule(false),resized=()=>schedule();
function clear(){
  cancelAnimationFrame(scheduled);scheduled=0;needsLayout=false;resize?.disconnect();resize=null;surfaceResize?.disconnect();surfaceResize=null;
  document.removeEventListener('scroll',scrolled,true);window.removeEventListener('resize',resized);
  frame?.remove();scene?.remove();workspace?.classList.remove('seasonal-framed');workspace?.removeAttribute('data-seasonal-theme');
  workspace=frame=canvas=scene=sidebar=mask=maskBase=maskHoles=null;surfaces=[];
}
export function syncSeasonalFrame(next,theme,paused){
  if(next!==workspace||!theme){clear();if(!theme||!next)return;workspace=next;
    frame=document.createElementNS(svgNS,'svg');frame.classList.add('seasonal-shell-decoration');frame.setAttribute('aria-hidden','true');
    const id='seasonal-frame-mask-'+(++serial);
    frame.innerHTML=`<defs><mask id="${id}" maskUnits="userSpaceOnUse" x="0" y="0" style="mask-type:luminance"><rect fill="white"/><g></g></mask></defs><g mask="url(#${id})" class="seasonal-frame-canvas"></g>`;
    mask=frame.querySelector('mask');maskBase=mask.firstElementChild;maskHoles=mask.lastElementChild;canvas=frame.querySelector('.seasonal-frame-canvas');workspace.prepend(frame);
    sidebar=workspace.querySelector(':scope>.sidebar');
    if(sidebar){scene=document.createElement('div');scene.className='seasonal-sidebar-scene';scene.setAttribute('aria-hidden','true');sidebar.append(scene);}
    surfaceResize=new ResizeObserver(resized);
    resize=new ResizeObserver(resized);for(const element of [workspace,workspace.querySelector(':scope>.content,:scope>.auth-main'),sidebar,sidebar?.querySelector('nav'),sidebar?.querySelector('.sidebar-bottom')])if(element)resize.observe(element);
    document.addEventListener('scroll',scrolled,{capture:true,passive:true});window.addEventListener('resize',resized,{passive:true});
  }
  if(!frame)return;
  workspace.classList.add('seasonal-framed');workspace.dataset.seasonalTheme=theme;
  if(frame.dataset.seasonalTheme!==theme||frame.dataset.compact!==String(innerWidth<=760)){
    frame.dataset.seasonalTheme=theme;frame.dataset.compact=String(innerWidth<=760);
    const compact=innerWidth<=760,auth=workspace.matches('.auth-layout');
    frame.dataset.seasonalParticles=themes.find(item=>item.id===theme)?.particles||'none';
    const sized=(svg,w,h=w)=>svg.replace('<svg ',`<svg width="${w}" height="${h}" style="width:${w}px;height:${h}px" `);
    canvas.innerHTML=seasonalParticles(frame.dataset.seasonalParticles,innerWidth,innerHeight)+['left','right'].map(side=>`<g class="seasonal-frame-${side}">${Array.from({length:3},(_,i)=>`<g class="seasonal-frame-piece"><g class="seasonal-edge-motion" style="--particle:${i}">${sized(holidaySymbol(theme,i),auth&&!compact?(i===1?80:104):compact?(i===1?40:48):(i===1?48:64))}</g></g>`).join('')}</g>`).join('')+`<g class="seasonal-ground-scene">${sized(holidayScene(theme),compact?170:220,compact?105:140)}</g>`;
    if(scene)scene.innerHTML=holidayScene(theme);
  }
  frame.classList.toggle('seasonal-paused',paused);scene?.classList.toggle('seasonal-paused',paused);
  schedule();
}
