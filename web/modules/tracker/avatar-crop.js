import {tText} from '../../core/i18n.js';
const t=key=>tText('tracker:'+key);
// A separate modal leaves the project form and its unsaved values intact.
export async function cropProjectAvatar(blob){
 const url=URL.createObjectURL(blob),image=new Image();
 try{
  await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(Error(t('invalidAvatar')));image.src=url;});
  return await new Promise(resolve=>{
   const dialog=document.createElement('dialog');dialog.className='tracker-dialog tracker-crop';
   dialog.innerHTML=`<header><h2>${t('cropAvatar')}</h2></header><canvas width="512" height="512" aria-label="${t('cropAvatar')}"></canvas><p>${t('cropHelp')}</p><label>${t('zoom')}<input type="range" min="1" max="3" value="1" step="0.01"></label><div class="tracker-actions"><button type="button" class="secondary" data-cancel>${t('cancel')}</button><button type="button" class="primary" data-apply>${t('apply')}</button></div>`;
   const canvas=dialog.querySelector('canvas'),context=canvas.getContext('2d');let zoom=1,x=0,y=0,drag=null;
   const scale=()=>Math.max(512/image.naturalWidth,512/image.naturalHeight)*zoom;
   const draw=()=>{const w=image.naturalWidth*scale(),h=image.naturalHeight*scale();x=Math.max(-(w-512)/2,Math.min((w-512)/2,x));y=Math.max(-(h-512)/2,Math.min((h-512)/2,y));context.clearRect(0,0,512,512);context.drawImage(image,(512-w)/2+x,(512-h)/2+y,w,h);};
   canvas.onpointerdown=e=>{canvas.setPointerCapture(e.pointerId);drag={x:e.clientX,y:e.clientY,ox:x,oy:y};};
   canvas.onpointermove=e=>{if(!drag)return;const ratio=512/canvas.getBoundingClientRect().width;x=drag.ox+(e.clientX-drag.x)*ratio;y=drag.oy+(e.clientY-drag.y)*ratio;draw();};
   canvas.onpointerup=canvas.onpointercancel=()=>drag=null;
   dialog.querySelector('input').oninput=e=>{zoom=Number(e.target.value);draw();};
   const close=value=>{dialog.close();dialog.remove();resolve(value);};
   dialog.addEventListener('cancel',e=>{e.preventDefault();close(null);});dialog.querySelector('[data-cancel]').onclick=()=>close(null);
   dialog.querySelector('[data-apply]').onclick=()=>{const result=document.createElement('canvas');result.width=result.height=256;result.getContext('2d').drawImage(canvas,0,0,256,256);result.toBlob(close,'image/png');};
   document.body.append(dialog);dialog.showModal();draw();
  });
 }finally{image.src='';URL.revokeObjectURL(url);}
}
