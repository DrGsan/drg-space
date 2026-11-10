import * as Y from 'yjs';

// One acknowledged delta in flight. New keystrokes stay in the Y.Doc and are
// merged into the next delta, instead of becoming an old snapshot backlog.
export function documentUpdates(doc,send,{onDirty=()=>{},delay=60}={}){
 let version=0,acknowledged=0,flight=null,vector=new Uint8Array([0]),active=false,disposed=false,timer;
 const schedule=(wait=delay)=>{if(!timer&&!disposed)timer=setTimeout(()=>{timer=null;flush();},wait);};
 function flush(){
  clearTimeout(timer);timer=null;
  if(disposed||!active||flight||version<=acknowledged)return;
  const update=Y.encodeStateAsUpdate(doc,vector);
  flight={seq:version,vector:Y.encodeStateVector(doc)};
  if(!send(update,version))flight=null;
 }
 const changed=(_update,origin)=>{if(origin==='remote'||disposed)return;version++;onDirty();schedule();};
 doc.on('update',changed);
 return {
  get version(){return version;},get acknowledged(){return acknowledged;},get dirty(){return version>acknowledged;},
  sync(state){vector=Y.encodeStateVectorFromUpdate(state);active=true;schedule(0);},
  offline(){active=false;flight=null;clearTimeout(timer);timer=null;},
  reject(){flight=null;},
  ack(seq){
   if(typeof seq!=='number'||!Number.isSafeInteger(seq)||!flight||seq<flight.seq||seq>version)return;
   acknowledged=Math.max(acknowledged,seq);vector=flight.vector;flight=null;schedule(0);
  },
  flush,
  destroy(){disposed=true;clearTimeout(timer);doc.off('update',changed);},
 };
}
