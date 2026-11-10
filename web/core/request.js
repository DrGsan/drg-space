import {tText} from "./i18n.js";
// Bound operations that may stall before headers, while reading a body, or
// while the browser decodes an image. Cancellation must always release the UI.
export async function abortable(operation,{signal,timeoutMs=0,message=tText("common:theServerDidNotRespondInTimeRetry")}={}){
  const controller=new AbortController();let timer,onAbort;
  const reason=()=>controller.signal.reason||new DOMException(tText("common:operationCancelled"),'AbortError');
  const forward=()=>controller.abort(signal.reason||new DOMException(tText("common:operationCancelled"),'AbortError'));
  try{
    const cancelled=new Promise((_,reject)=>{onAbort=()=>reject(reason());controller.signal.addEventListener('abort',onAbort,{once:true});});
    signal?.addEventListener('abort',forward,{once:true});if(signal?.aborted)forward();
    if(timeoutMs>0)timer=setTimeout(()=>controller.abort(new DOMException(message,'TimeoutError')),timeoutMs);
    const work=Promise.resolve().then(()=>{if(controller.signal.aborted)throw reason();return operation(controller.signal);});
    return await Promise.race([work,cancelled]);
  }finally{clearTimeout(timer);signal?.removeEventListener('abort',forward);controller.signal.removeEventListener('abort',onAbort);}
}
