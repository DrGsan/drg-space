// Refresh only at a known server deadline or when returning to this view.
// A pending maintenance job backs off; idle and hidden views do not poll.
export function watchFileDeadlines({connected,blocked,refresh,clock=Date.now,doc=document,events=window}){
 let timer,deadline=null,offset=0,attempt=0,running=false,disposed=false,last=clock();
 const clear=()=>{clearTimeout(timer);timer=null;};
 const plan=()=>{
  clear();if(disposed||running||!connected()||doc.hidden||blocked()||deadline===null)return;
  const remaining=deadline*1000-(clock()+offset);
  const delay=remaining>0?remaining+1000:Math.min(60000,1000*15*2**Math.min(attempt,2));
  timer=setTimeout(run,Math.min(2147483647,delay));
 };
 const run=async()=>{
  clear();if(disposed||running||!connected()||doc.hidden||blocked())return;
  running=true;if(deadline!==null&&deadline*1000<=clock()+offset)attempt++;
  try{await refresh();last=clock();}catch{/* Keep the current list; retry the pending deadline later. */}
  finally{running=false;plan();}
 };
 const wake=()=>{
  if(disposed||doc.hidden||!connected())return;
  if(clock()-last>=15000||deadline!==null&&deadline*1000<=clock()+offset)run();else plan();
 };
 doc.addEventListener('visibilitychange',wake);events.addEventListener('focus',wake);events.addEventListener('online',wake);
 return {
  set(value,serverTime){
   if(value!==deadline){deadline=value??null;attempt=0;}
   if(Number.isFinite(serverTime))offset=serverTime*1000-clock();
   plan();
  },
  resume:plan,
  destroy(){disposed=true;clear();doc.removeEventListener('visibilitychange',wake);events.removeEventListener('focus',wake);events.removeEventListener('online',wake);}
 };
}
