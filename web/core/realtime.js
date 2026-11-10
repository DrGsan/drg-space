import { csrfCookie } from './auth.js';
// Same-origin cookie authentication; secrets never appear in socket URLs.
export function connectRoom(path, ctx, options = {}) {
  let socket, disposed=false, timer, ping, attempt=0, connected=false;
  const clientId=options.clientId ?? Math.floor(Math.random()*2**32);
  const start=async()=>{
    if(disposed)return;
    try {
      await ctx.api('/me');
      if(options.validate)await options.validate(); // Rotates an expired JWT before each reconnect.
      if(disposed)return;
      socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/api/live/${path}`);
      socket.onopen=()=>socket.send(JSON.stringify({...options.state?.(),type:'auth',csrf:csrfCookie(),client_id:clientId}));
      socket.onmessage=event=>{
        try {
          const data=JSON.parse(event.data);
          if(['ready','sync','init'].includes(data.type)){
            connected=true;attempt=0;options.onStatus?.('online');
            clearInterval(ping);ping=setInterval(()=>send({...options.state?.(),type:'ping'}),options.pingInterval??20000);
          }
          if(data.type==='error')options.onError?.(data);
          else options.onMessage?.(data);
        } catch(e){options.onError?.({message:e.message,status:422});}
      };
      socket.onclose=event=>{
        connected=false;clearInterval(ping);
        if(disposed)return;
        if(event.code===4409){options.onReset?.();return;}
        options.onStatus?.('offline');
        timer=setTimeout(start,Math.min(15000,500*2**Math.min(attempt++,5)));
      };
      socket.onerror=()=>{};
    } catch(e){
      options.onStatus?.('offline');if(e.status)options.onError?.({message:e.message,status:e.status});
      if(!disposed && ![401,403,404].includes(e.status))timer=setTimeout(start,5000);
    }
  };
  function send(value){
    if(!connected || socket?.readyState!==WebSocket.OPEN)return false;
    socket.send(JSON.stringify(value));return true;
  }
  start();
  return {send,get online(){return connected;},destroy(){disposed=true;clearTimeout(timer);clearInterval(ping);socket?.close();}};
}
