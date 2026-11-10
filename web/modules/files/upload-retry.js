import {tText} from "../../core/i18n.js";
export class UploadStoppedError extends Error {
 constructor(){super(tText("files:uploadStopped"));this.name='UploadStoppedError';}
}

// Only an explicit rejection before writing is safe to retry. A lost response
// or a 5xx can hide a committed file, so neither is retried here.
export async function withUploadRateRetry(send,transfer){
 for(let attempt=0;;attempt++){
  if(transfer.isStopped())throw new UploadStoppedError();
  try{return await send();}catch(error){
   const raw=error.retryAfter,seconds=typeof raw==='string'&&/^\d+$/.test(raw)?Number(raw):NaN;
   if(error.status!==429||!Number.isFinite(seconds)||seconds<1||seconds>3600||attempt>=3)throw error;
   const deadline=performance.now()+seconds*1000;
   while(performance.now()<deadline){
    if(transfer.isStopped())throw new UploadStoppedError();
    transfer.wait(Math.max(1,Math.ceil((deadline-performance.now())/1000)));
    await new Promise(resolve=>setTimeout(resolve,Math.min(200,Math.max(0,deadline-performance.now()))));
   }
   if(transfer.isStopped())throw new UploadStoppedError();
   transfer.resume();
  }
 }
}
