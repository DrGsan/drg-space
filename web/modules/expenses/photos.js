import {tText,tHtml} from '../../core/i18n.js';
import {$,esc} from '../../core/ui.js';
import {modal} from '../../core/modal.js';
import './photos.css';
const base=(event,token)=>token?'/api/shared-expenses/'+encodeURIComponent(token):'/api/expenses/'+event.id;
export function expensePhotos(event,editable=false,token=null){
 if(!event.photos?.length)return '';
 const path=base(event,token);
 return `<section class="expense-photos"><button type="button" class="expense-photo-stack" data-open-expense-photos aria-label="${tHtml('expenses:openGallery',{count:event.photos.length})}">${event.photos.slice(0,3).map((id,index)=>`<span class="expense-photo-cover" data-cover-index="${index}" style="--photo-position:${index};--photo-count:${Math.min(event.photos.length,3)}"><img src="${path}/photos/${id}?preview=true" alt="" loading="lazy" width="200" height="170"></span>`).join('')}</button><div class="expense-photo-actions"><button type="button" class="text-link" data-open-expense-photos>${tHtml('expenses:openGallery',{count:event.photos.length})}</button><a class="text-link" href="${path}/photos.zip" download>${tHtml('expenses:downloadAllPhotos')}</a></div></section>`;
}
export function bindExpensePhotos(host,event,{token=null,remove=null}={}){
 const path=base(event,token);
 host.querySelectorAll('[data-open-expense-photos]').forEach(button=>{
  if(button.classList.contains('expense-photo-stack')){
   const covers=[...button.querySelectorAll('[data-cover-index]')];
   const clear=()=>{delete button.dataset.hoverPhoto;covers.forEach(cover=>cover.classList.remove('is-photo-hovered'));};
   button.addEventListener('pointermove',event=>{
    if(event.pointerType!=='mouse'||!matchMedia('(hover:hover) and (pointer:fine)').matches)return;
    const bounds=button.getBoundingClientRect(),spacing=innerWidth<=700?43:66,index=Math.max(0,Math.min(covers.length-1,Math.round((event.clientX-bounds.x-bounds.width/2)/spacing+(covers.length-1)/2)));
    button.dataset.hoverPhoto=String(index);covers.forEach((cover,i)=>cover.classList.toggle('is-photo-hovered',i===index));
   });button.addEventListener('pointerleave',clear);button.addEventListener('blur',clear);
  }
  button.onclick=clickEvent=>{
  let index=Number(button.dataset.hoverPhoto??clickEvent.target.closest('[data-cover-index]')?.dataset.coverIndex??0),generation=0,closed=false,start;
  const dialog=modal(`<h2 data-photo-heading></h2><div class="expense-gallery-image"><button type="button" data-previous aria-label="${tHtml('expenses:previousPhoto')}">‹</button><img data-full-photo alt=""><button type="button" data-next aria-label="${tHtml('expenses:nextPhoto')}">›</button></div><div class="expense-gallery-thumbs">${event.photos.map((id,i)=>`<button type="button" data-photo-index="${i}" aria-label="${tHtml('expenses:eveningPhoto2',{value0:i+1})}"><img src="${path}/photos/${id}?preview=true" alt="" loading="lazy" width="48" height="48"></button>`).join('')}</div><footer class="expense-photo-actions"><a class="secondary" data-download-photo download>${tHtml('expenses:downloadCurrentPhoto')}</a><a class="text-link" href="${path}/photos.zip" download>${tHtml('expenses:downloadAllPhotos')}</a>${remove?`<button type="button" class="text-link danger" data-delete-photo>${tHtml('expenses:delete2')}</button>`:''}</footer>`);
  dialog.classList.add('expense-gallery-overlay');
  const image=$('[data-full-photo]',dialog);
  async function show(next){
   index=(next+event.photos.length)%event.photos.length;const request=++generation,id=event.photos[index],url=path+'/photos/'+id;
   $('[data-photo-heading]',dialog).textContent=tText('expenses:photoPosition',{index:index+1,count:event.photos.length});$('[data-download-photo]',dialog).href=url+'?download=true';
   dialog.querySelectorAll('[data-photo-index]').forEach(button=>button.setAttribute('aria-pressed',String(Number(button.dataset.photoIndex)===index)));
   const loaded=new Image();loaded.src=url;try{await loaded.decode();}catch{}if(closed||request!==generation)return;
   image.src=url;image.alt=tText('expenses:photoPosition',{index:index+1,count:event.photos.length});if(!matchMedia('(prefers-reduced-motion:reduce)').matches)image.animate([{opacity:.45},{opacity:1}],{duration:280,easing:'ease-out'});
   if(event.photos.length>1){const ahead=new Image();ahead.src=path+'/photos/'+event.photos[(index+1)%event.photos.length];}
  }
  $('[data-previous]',dialog).onclick=()=>show(index-1);$('[data-next]',dialog).onclick=()=>show(index+1);
  dialog.querySelectorAll('[data-photo-index]').forEach(button=>button.onclick=()=>show(Number(button.dataset.photoIndex)));
  const key=event=>{if([...document.querySelectorAll('.modal-overlay')].at(-1)!==dialog)return;if(event.key==='ArrowLeft'){event.preventDefault();show(index-1);}if(event.key==='ArrowRight'){event.preventDefault();show(index+1);}};
  document.addEventListener('keydown',key);dialog.addEventListener('close',()=>{closed=true;document.removeEventListener('keydown',key);});
  image.onpointerdown=e=>start={x:e.clientX,y:e.clientY};image.onpointerup=e=>{if(start&&Math.abs(e.clientX-start.x)>50&&Math.abs(e.clientX-start.x)>Math.abs(e.clientY-start.y))show(index+(e.clientX<start.x?1:-1));start=null;};image.ondragstart=e=>e.preventDefault();
  $('[data-delete-photo]',dialog)?.addEventListener('click',async()=>{const button=$('[data-delete-photo]',dialog);button.disabled=true;try{await remove(event.photos[index]);$('.modal-close',dialog).click();}finally{button.disabled=false;}});
  show(index);
  };
 });
}
