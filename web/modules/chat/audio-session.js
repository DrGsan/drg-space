// AudioSession is page-wide. Overlapping capture/teardown operations must not
// restore the media profile while another call still owns it.
const sessions=new WeakMap();
export function acquireCallAudioSession(session){
  if(!session||typeof session.type!=='string')return ()=>{};
  let lease=sessions.get(session);
  if(!lease){
    lease={previous:session.type,count:0};
    try{session.type='play-and-record';}catch{return ()=>{};}
    if(session.type!=='play-and-record')return ()=>{};
    sessions.set(session,lease);
  }
  lease.count++;let released=false;
  return ()=>{
    if(released)return;released=true;
    if(--lease.count)return;
    sessions.delete(session);
    // Respect a profile subsequently chosen by another media feature.
    if(session.type==='play-and-record')try{session.type=lease.previous;}catch{}
  };
}
