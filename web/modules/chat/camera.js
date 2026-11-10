import {tText,tHtml} from "../../core/i18n.js";
import './camera.css';

export function cameraCapture(ctx,{onFile,isCurrent=()=>true}){
  const modal=ctx.modal(`<h2>${tHtml("chat:camera")}</h2><div class="chat-camera-preview"><video autoplay muted playsinline aria-label="${tHtml("chat:cameraImage")}"></video></div><p class="muted small" data-camera-status role="status">${tHtml("chat:connectingCamera")}</p><div class="chat-camera-actions"><button type="button" class="primary" data-take-photo disabled>${tHtml("chat:takePhoto")}</button><button type="button" class="secondary" data-retry-camera hidden>${tHtml("chat:retry")}</button></div>`);
  const video=modal.querySelector('video'),status=modal.querySelector('[data-camera-status]'),take=modal.querySelector('[data-take-photo]'),retry=modal.querySelector('[data-retry-camera]');
  let stream,closed=false,request=0;
  const stop=()=>{stream?.getTracks().forEach(track=>track.stop());stream=null;video.srcObject=null;};
  const close=()=>{if(closed)return;closed=true;request++;stop();observer.disconnect();document.removeEventListener('visibilitychange',visibility);if(modal.isConnected)modal.querySelector('.modal-close').click();};
  const observer=new MutationObserver(()=>{if(!modal.isConnected||!isCurrent())close();});observer.observe(document.body,{childList:true,subtree:true});
  const visibility=()=>{if(document.hidden)close();};document.addEventListener('visibilitychange',visibility);
  const ready=()=>{if(!closed&&video.videoWidth&&video.videoHeight){take.disabled=false;status.textContent=tText("chat:thePhotoWillAppearInTheAttachmentsSend");}};
  video.addEventListener('loadeddata',ready);video.addEventListener('resize',ready);
  async function start(){
    const generation=++request;stop();take.disabled=true;retry.hidden=true;status.textContent=tText("chat:connectingCamera");
    try{
      if(!navigator.mediaDevices?.getUserMedia)throw new Error(tText("chat:cameraIsNotAvailableInThisBrowser"));
      const captured=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1920},height:{ideal:1080}},audio:false});
      if(closed||generation!==request||!isCurrent()){captured.getTracks().forEach(track=>track.stop());return;}
      stream=captured;video.srcObject=stream;await video.play();ready();
    }catch(error){
      if(closed||generation!==request)return;stop();retry.hidden=false;
      status.textContent=error.name==='NotAllowedError'?tText("chat:allowCameraAccessInTheBrowserSettingsAnd"):error.name==='NotFoundError'?tText("chat:cameraNotFoundConnectItAndRetry"):error.name==='NotReadableError'?tText("chat:theCameraIsInUseByAnotherApplication"):error.message||tText("chat:couldNotEnableCamera");
    }
  }
  retry.onclick=start;
  take.onclick=async()=>{
    if(closed||!isCurrent()||!video.videoWidth||!video.videoHeight)return;
    take.disabled=true;
    try{
      const canvas=document.createElement('canvas'),scale=Math.min(1,1920/Math.max(video.videoWidth,video.videoHeight));
      canvas.width=Math.round(video.videoWidth*scale);canvas.height=Math.round(video.videoHeight*scale);
      canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.9));
      if(closed||!isCurrent())return;if(!blob)throw new Error(tText("chat:couldNotSavePhotoTryAgain"));
      const file=new File([blob],tText("chat:photoJpg", {value0:(new Date().toISOString().replaceAll(':','-'))}),{type:'image/jpeg'});
      close();onFile(file);
    }catch(error){if(!closed){status.textContent=error.message;take.disabled=false;}}
  };
  start();return {close};
}
