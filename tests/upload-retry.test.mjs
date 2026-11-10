import test from 'node:test';
import assert from 'node:assert/strict';
import {withUploadRateRetry,UploadStoppedError} from '../web/modules/files/upload-retry.js';

test('429 waits for Retry-After and continues the same queued operation',async()=>{
 let calls=0,resumed=0;const waits=[];
 const transfer={isStopped:()=>false,wait:n=>waits.push(n),resume:()=>resumed++};
 const result=await withUploadRateRetry(async()=>{if(!calls++)throw Object.assign(Error('Rate'),{status:429,retryAfter:'1'});return 'saved';},transfer);
 assert.equal(result,'saved');assert.equal(calls,2);assert.equal(resumed,1);assert.ok(waits.length);assert.ok(waits.every(n=>n===1));
});

test('Stop interrupts backoff without another POST',async()=>{
 let calls=0,stopped=false;
 const transfer={isStopped:()=>stopped,wait:()=>{stopped=true;},resume:()=>assert.fail('must not resume')};
 await assert.rejects(withUploadRateRetry(async()=>{calls++;throw Object.assign(Error('Rate'),{status:429,retryAfter:'60'});},transfer),UploadStoppedError);
 assert.equal(calls,1);
});

test('ambiguous failures and invalid Retry-After never repeat a POST',async()=>{
 const transfer={isStopped:()=>false,wait:()=>assert.fail('must not wait')};
 for(const fields of [{},{status:503},{status:429},{status:429,retryAfter:'0'},{status:429,retryAfter:'NaN'},{status:429,retryAfter:'7200'}]){
  let calls=0;const error=Object.assign(Error('Rejected'),fields);
  await assert.rejects(withUploadRateRetry(async()=>{calls++;throw error;},transfer),e=>e===error);assert.equal(calls,1);
 }
});
