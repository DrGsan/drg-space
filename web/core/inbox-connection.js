// A live subscription is account-scoped and must prove server-side call events.
let current;
export function registerInboxConnection(uid,live){
  const entry={uid,live};current=entry;
  return ()=>{if(current===entry)current=null;};
}
export function hasLiveCallInbox(uid){return current?.uid===uid&&current.live();}
