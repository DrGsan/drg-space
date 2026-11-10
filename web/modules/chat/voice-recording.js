import {tText,tHtml} from '../../core/i18n.js';
import {chatIcon} from './icons.js';
import './recordings.css';

const MAX_BYTES=50*1024*1024;
export function recordingWaveform(samples){
  if(!samples.length)return [];
  return Array.from({length:Math.min(64,samples.length)},(_,i)=>{
    const a=Math.floor(i*samples.length/Math.min(64,samples.length)),b=Math.max(a+1,Math.floor((i+1)*samples.length/Math.min(64,samples.length)));
    return Math.round(Math.min(31,Math.max(...samples.slice(a,b))*31));
  });
}

// One recorder for both transports. Nothing is uploaded or sent before Add.
export function voiceRecording(ctx,{onFile,isCurrent=()=>true,kind='voice',autoStart=false,held=false}){
  const circle=kind==='video_note',limit=circle?60:600;
  const modal=ctx.modal(`<h2>${tHtml(circle?'chat:videoNote':'chat:voiceMessage')}</h2><div class="chat-recording-stage ${circle?'is-circle':''}"><video data-recording-live muted playsinline hidden></video><canvas data-recording-canvas width="480" height="480" hidden></canvas>${circle?'':`<div class="chat-recording-meter" aria-hidden="true">${Array.from({length:24},(_,i)=>`<i style="--meter-index:${i}"></i>`).join('')}</div>`}<span data-voice-time class="chat-voice-time">0:00</span></div><${circle?'video':'audio'} data-voice-preview class="chat-recording-preview ${circle?'is-circle':''}" controls playsinline preload="metadata" hidden aria-label="${tHtml('chat:playRecording')}"></${circle?'video':'audio'}><p class="muted small" data-voice-status role="status">${tHtml(circle?'chat:videoNoteLimit':'chat:upTo10MinutesTheRecordingWillAppear')}</p><p class="chat-recording-gestures" data-recording-gestures hidden>${tHtml('chat:recordingGestures')}</p><div class="chat-camera-actions"><button type="button" class="primary" data-voice-start>${tHtml('chat:startRecording')}</button><button type="button" class="secondary" data-voice-stop hidden>${tHtml('chat:stopRecording')}</button><button type="button" class="secondary" data-recording-cancel>${tHtml('chat:cancelRecording')}</button><button type="button" class="secondary" data-voice-again hidden>${tHtml('chat:recordAgain')}</button><button type="button" class="primary" data-voice-attach hidden>${tHtml('chat:addToMessage')}</button></div>`);
  const q=s=>modal.querySelector(s),start=q('[data-voice-start]'),stop=q('[data-voice-stop]'),again=q('[data-voice-again]'),attach=q('[data-voice-attach]'),preview=q('[data-voice-preview]'),live=q('[data-recording-live]'),canvas=q('canvas'),stage=q('.chat-recording-stage'),status=q('[data-voice-status]'),timer=q('[data-voice-time]'),gestures=q('[data-recording-gestures]');
  let stream,encoded,recorder,context,analyser,closed=false,generation=0,clock,frame,started=0,ended=0,parts=[],size=0,file,metadata,url,busy=false,waiting=false,pendingStop=false,locked=!held,samples=[];
  const announce=active=>document.dispatchEvent(new CustomEvent('drg-recording-capture',{detail:{active}}));
  const release=()=>{clearInterval(clock);cancelAnimationFrame(frame);live.pause();live.srcObject=null;stream?.getTracks().forEach(track=>track.stop());encoded?.getTracks().forEach(track=>track.stop());stream=encoded=null;context?.close().catch(()=>{});context=analyser=null;};
  const clear=()=>{preview.pause();preview.removeAttribute('src');preview.load();preview.hidden=true;stage.hidden=false;live.hidden=true;if(url)URL.revokeObjectURL(url);url=null;file=metadata=null;parts=[];samples=[];size=0;timer.textContent='0:00';};
  const close=()=>{if(closed)return;closed=true;generation++;announce(false);if(recorder?.state==='recording')recorder.stop();release();clear();observer.disconnect();document.removeEventListener('visibilitychange',visibility);if(modal.isConnected)q('.modal-close').click();};
  const finish=()=>{if(waiting){pendingStop=true;return;}if(recorder?.state==='recording'){ended=performance.now();recorder.stop();}release();};
  const visibility=()=>{if(document.hidden){if(waiting)close();else finish();preview.pause();}};
  const observer=new MutationObserver(()=>{if(!modal.isConnected||!isCurrent())close();else if(document.querySelector('.voice-call')){if(waiting)close();else finish();}});observer.observe(document.body,{childList:true,subtree:true});
  document.addEventListener('visibilitychange',visibility);
  async function begin(){
    if(busy||closed||waiting||recorder?.state==='recording')return;
    if(document.querySelector('.voice-call')){status.textContent=tText('chat:finishTheCallBeforeRecordingTheVoice');return;}
    const token=++generation;start.disabled=true;waiting=true;pendingStop=false;status.textContent=tText(circle?'chat:connectingCamera':'chat:connectingMicrophone');clear();
    try{
      if(!navigator.mediaDevices?.getUserMedia||!globalThis.MediaRecorder)throw Error(tText('chat:voiceRecordingIsNotAvailableInThisBrowser'));
      const captured=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true},video:circle?{facingMode:'user',width:{ideal:480},height:{ideal:480},frameRate:{ideal:30,max:30}}:false});
      if(closed||token!==generation||!isCurrent()||document.hidden){captured.getTracks().forEach(track=>track.stop());return;}
      stream=captured;
      const types=circle?['video/mp4;codecs=avc1.42E01E,mp4a.40.2','video/mp4','video/webm;codecs=vp8,opus']:['audio/mp4','audio/webm;codecs=opus','audio/ogg;codecs=opus'];
      const type=types.find(type=>MediaRecorder.isTypeSupported(type));if(!type)throw Error(tText('chat:theBrowserDoesNotSupportTheAppropriateAudio'));
      if(circle){
        live.hidden=false;live.srcObject=stream;await live.play();
        if(closed||token!==generation||!isCurrent()){release();return;}
        const painter=canvas.getContext('2d',{alpha:false});let last=0;
        const draw=time=>{if(!stream)return;if(time-last>=1000/30&&live.videoWidth){last=time;const side=Math.min(live.videoWidth,live.videoHeight);painter.drawImage(live,(live.videoWidth-side)/2,(live.videoHeight-side)/2,side,side,0,0,480,480);}frame=requestAnimationFrame(draw);};draw(performance.now());
        encoded=canvas.captureStream(30);for(const track of stream.getAudioTracks())encoded.addTrack(track);
      }else{
        const Audio=globalThis.AudioContext||globalThis.webkitAudioContext;
        if(Audio){context=new Audio();await context.resume();analyser=context.createAnalyser();analyser.fftSize=256;context.createMediaStreamSource(stream).connect(analyser);}
      }
      if(closed||token!==generation||!isCurrent()){release();return;}
      recorder=new MediaRecorder(encoded||stream,{mimeType:type,audioBitsPerSecond:64000,...(circle?{videoBitsPerSecond:1000000}:{})});
      recorder.ondataavailable=event=>{if(event.data.size){parts.push(event.data);size+=event.data.size;if(size>=MAX_BYTES)finish();}};
      recorder.onerror=()=>{status.textContent=tText('chat:couldNotRecordTheSoundTryAgain');finish();};
      recorder.onstop=()=>{
        if(!ended)ended=performance.now();release();if(closed||token!==generation)return;
        stop.hidden=gestures.hidden=true;start.disabled=false;again.hidden=false;
        const duration=Math.round(Math.min(limit*1000,ended-started));
        if(!size||size>MAX_BYTES||duration<300){status.textContent=tText('chat:theRecordingIsTooShortOrExceeds50');start.hidden=false;return;}
        const mime=recorder.mimeType,ext=mime.includes('mp4')?(circle?'mp4':'m4a'):mime.includes('ogg')?'ogg':'webm';
        file=new File(parts,`${circle?'video-note':'voice'}-${Date.now()}.${ext}`,{type:mime});parts=[];
        metadata={kind,duration_ms:duration,...(!circle?{waveform:recordingWaveform(samples)}:{})};
        url=URL.createObjectURL(file);preview.src=url;preview.hidden=false;stage.hidden=true;attach.hidden=false;status.textContent=tText('chat:listenToTheRecordingAndAddItTo');
      };
      ended=0;started=performance.now();recorder.start(250);waiting=false;announce(true);start.hidden=true;again.hidden=attach.hidden=true;stop.hidden=false;gestures.hidden=locked;status.textContent=tText(locked?'chat:recordingLocked':'chat:recordingInProgress');
      const buffer=analyser?new Uint8Array(analyser.fftSize):null;
      clock=setInterval(()=>{const seconds=Math.floor((performance.now()-started)/1000);timer.textContent=Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0');if(analyser){analyser.getByteTimeDomainData(buffer);const rms=Math.sqrt(buffer.reduce((s,n)=>s+((n-128)/128)**2,0)/buffer.length),level=Math.min(1,Math.sqrt(rms)*2);samples.push(level);stage.style.setProperty('--recording-level',level);}
        if(seconds>=limit)finish();},100);
      for(const track of stream.getTracks())track.onended=finish;
      if(pendingStop)finish();
    }catch(error){release();if(!closed){start.disabled=false;status.textContent=error.name==='NotAllowedError'?tText(circle?'chat:allowCameraAndMicrophone':'chat:allowMicrophoneAccessInBrowserSettings'):error.message;}}
    finally{waiting=false;}
  }
  start.onclick=()=>{locked=true;begin();};stop.onclick=finish;q('[data-recording-cancel]').onclick=close;
  again.onclick=()=>{if(busy)return;generation++;finish();clear();start.hidden=false;start.disabled=false;again.hidden=attach.hidden=true;status.textContent=tText(circle?'chat:videoNoteLimit':'chat:upTo10MinutesTheRecordingWillAppear2');};
  attach.onclick=async()=>{
    if(busy||!file||closed||!isCurrent())return;busy=true;preview.pause();attach.disabled=again.disabled=true;status.textContent=tText('chat:addingAttachment');
    try{if(await onFile(file,metadata)===true){close();return;}status.textContent=tText('chat:couldNotAddTheRecordingItIsSaved');}
    catch(error){if(!closed)status.textContent=error.message;}
    finally{busy=false;if(!closed)attach.disabled=again.disabled=false;}
  };
  announce(true);
  if(autoStart)begin();
  return {close,finish,lock(){locked=true;gestures.hidden=true;if(recorder?.state==='recording')status.textContent=tText('chat:recordingLocked');},hint(cancel){modal.classList.toggle('is-recording-cancel',cancel);},get closed(){return closed;}};
}

// Pointer capture keeps the hold stable even when the preview dialog appears.
export function recordingActions(form,ctx,{onFile,isCurrent,canRecord=()=>true,onOpen=()=>{}}){
  const controls=form.querySelector('[data-recording-actions]'),button=form.querySelector('[data-recording-hold]'),switcher=form.querySelector('[data-recording-switch]'),input=form.querySelector('textarea'),send=form.querySelector('[type=submit]');
  const events=new AbortController();let kind='voice',session,gesture;
  const update=()=>{const hasContent=!!input.value.trim()||!!form.querySelector('[data-chat-drafts] [data-remove-draft]')||!form.querySelector('[data-chat-edit]').hidden;controls.hidden=hasContent;send.hidden=!hasContent;button.disabled=!canRecord();};
  const open=(held=false)=>{if(!canRecord())return;onOpen();session?.close();session=voiceRecording(ctx,{kind,onFile,isCurrent,autoStart:true,held});return session;};
  switcher.onclick=()=>{kind=kind==='voice'?'video_note':'voice';button.innerHTML=chatIcon(kind==='voice'?'mic':'video');button.setAttribute('aria-label',tText(kind==='voice'?'chat:voiceMessage':'chat:videoNote'));switcher.innerHTML=chatIcon(kind==='voice'?'video':'mic');switcher.setAttribute('aria-label',tText(kind==='voice'?'chat:switchToVideoNote':'chat:switchToVoice'));};
  button.addEventListener('pointerdown',event=>{if(event.button!==0||!canRecord())return;event.preventDefault();const recording=open(true);if(!recording)return;gesture={id:event.pointerId,x:event.clientX,y:event.clientY,time:performance.now(),recording,cancel:false};button.setPointerCapture(event.pointerId);},{signal:events.signal});
  button.addEventListener('pointermove',event=>{if(!gesture||event.pointerId!==gesture.id)return;const dx=event.clientX-gesture.x,dy=event.clientY-gesture.y;gesture.cancel=dx< -80;gesture.recording.hint(gesture.cancel);if(dy< -70&&!gesture.cancel){gesture.recording.lock();gesture=null;}},{signal:events.signal});
  button.addEventListener('pointerup',event=>{if(!gesture||event.pointerId!==gesture.id)return;const current=gesture;gesture=null;if(current.cancel)current.recording.close();else if(performance.now()-current.time<250)current.recording.lock();else current.recording.finish();},{signal:events.signal});
  button.addEventListener('pointercancel',()=>{gesture?.recording.close();gesture=null;},{signal:events.signal});
  button.addEventListener('click',event=>{event.preventDefault();if(event.detail===0)open();},{signal:events.signal});
  input.addEventListener('input',update,{signal:events.signal});form.addEventListener('drg-composer-change',update,{signal:events.signal});
  const observer=new MutationObserver(update);observer.observe(form.querySelector('[data-chat-drafts]'),{childList:true,subtree:true});observer.observe(form.querySelector('[data-chat-edit]'),{attributes:true,attributeFilter:['hidden']});
  update();return {update,close(){session?.close();},destroy(){session?.close();events.abort();observer.disconnect();}};
}
