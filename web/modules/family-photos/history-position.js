// Keep a visible source message fixed through prepends and deferred media layout.
export function historyPosition(history,{follow=false,pinBottom=true}={}){
  const top=history.scrollTop,height=history.scrollHeight,box=history.getBoundingClientRect();
  const bottom=follow||pinBottom&&top+history.clientHeight>=height-100;
  const rows=[...history.querySelectorAll('[data-fp-message-id],[data-fp-pending]')];
  let lo=0,hi=rows.length;
  while(lo<hi){const mid=(lo+hi)>>1;if(rows[mid].getBoundingClientRect().bottom<=box.top)lo=mid+1;else hi=mid;}
  const anchors=rows.slice(lo,lo+3).map(row=>{
    const parts=[...row.querySelectorAll('.chat-reply-quote,.chat-media-album,.chat-message-text,.chat-attachments,[data-recording]')];
    const node=parts.find(part=>part.getBoundingClientRect().bottom>box.top)||row;
    return {node,offset:node.getBoundingClientRect().top-box.top};
  });
  return ()=>{
    if(!history.isConnected)return;
    if(bottom){if(Math.abs(history.scrollHeight-history.clientHeight-history.scrollTop)>.5)history.scrollTop=history.scrollHeight;return;}
    const anchor=anchors.find(v=>history.contains(v.node));
    if(anchor){const delta=anchor.node.getBoundingClientRect().top-history.getBoundingClientRect().top-anchor.offset;if(Math.abs(delta)>.5)history.scrollTop+=delta;}
    else if(Math.abs(history.scrollTop-top)>.5)history.scrollTop=top;
  };
}
