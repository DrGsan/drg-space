import {tText} from "../../core/i18n.js";
export class UploadStoppedError extends Error {
 constructor(){super(tText("files:uploadStopped"));this.name='UploadStoppedError';}
}

// Only explicit rejections before the file record is committed can be retried.
export async function withUploadRateRetry(send,transfer){
 let rateRetries=0;
 for(;;){
  if(transfer.isStopped())throw new UploadStoppedError();
  try{return await send();}catch(error){
   if(error.status===503&&error.code==='fileStorageTemporarilyUnavailable'&&transfer.waitForStorage){
    if(!await transfer.waitForStorage(error.message))throw new UploadStoppedError();
    transfer.resume();continue;
   }
   const raw=error.retryAfter,seconds=typeof raw==='string'&&/^\d+$/.test(raw)?Number(raw):NaN;
   if(error.status!==429||!Number.isFinite(seconds)||seconds<1||seconds>3600||rateRetries>=3)throw error;
   rateRetries++;
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
