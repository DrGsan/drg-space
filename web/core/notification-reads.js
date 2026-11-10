// Only server-confirmed chat watermarks affect message-related notices.
export function applyMessageReads(items,reads){
 for(const item of items){
  if(!['message','family_message'].includes(item.kind))continue;
  const through=reads[item.payload?.space_id],mid=item.payload?.message_id;
  if(Number.isSafeInteger(through)&&Number.isSafeInteger(mid)&&mid>0&&mid<=through)item.read=true;
 }
}
export async function syncDeliveredReads(account,reads,env=globalThis){
 try{
  const registration=await env.navigator?.serviceWorker?.getRegistration('/');
  if(registration?.active&&new URL(registration.active.scriptURL,env.location.origin).pathname==='/push-sw.js')
   registration.active.postMessage({type:'drg-chat-reads',account_id:account,reads});
 }catch{}
}
