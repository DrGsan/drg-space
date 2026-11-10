// Synthetic touch sequences work in both desktop engines. A separate Chromium
// check exercises trusted touchscreen input through its device protocol.
export async function touchSequence(page,points,{selector=null,end='touchend',fingers=1}={}){
  await page.evaluate(({points,selector,end,fingers})=>{
    const target=selector?document.querySelector(selector):document.elementFromPoint(points[0][0],points[0][1]);
    const fire=(type,x,y,active)=>{
      const point={identifier:1,target,clientX:x,clientY:y};
      const event=new Event(type,{bubbles:true,cancelable:true});
      Object.defineProperties(event,{touches:{value:active?Array.from({length:fingers},(_,i)=>({...point,identifier:i+1})):[]},changedTouches:{value:[point]}});
      target.dispatchEvent(event);
    };
    points.forEach(([x,y],i)=>fire(i?'touchmove':'touchstart',x,y,true));
    if(end)fire(end,...points.at(-1),false);
  },{points,selector,end,fingers});
}

export async function touchEvent(page,type,[x,y],{selector='#pad',fingers=1}={}){
  await page.evaluate(({type,x,y,selector,fingers})=>{
    const target=document.querySelector(selector),point={identifier:1,target,clientX:x,clientY:y};
    const event=new Event(type,{bubbles:true,cancelable:true});
    const active=!['touchend','touchcancel'].includes(type);
    Object.defineProperties(event,{touches:{value:active?Array.from({length:fingers},(_,i)=>({...point,identifier:i+1})):[]},changedTouches:{value:[point]}});
    target.dispatchEvent(event);
  },{type,x,y,selector,fingers});
}
