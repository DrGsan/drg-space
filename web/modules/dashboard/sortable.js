// Move existing DOM nodes: clocks, editors and pending widget requests stay alive.
export function sortableWidgets(grid, changed) {
  let unlocked=false, moving=null, startOrder=null, touchId=null,dropTarget=null;
  const items=()=>[...grid.querySelectorAll(':scope > [data-widget-id]')];
  const order=()=>items().map(item=>item.dataset.widgetId);
  const finish=()=>{
    if(!moving)return;
    if(dropTarget?.target.isConnected)grid.insertBefore(moving,dropTarget.after?dropTarget.target.nextSibling:dropTarget.target);
    dropTarget?.target.classList.remove('drop-target');dropTarget=null;
    moving.classList.remove('widget-moving');moving=null;touchId=null;
    const next=order();if(JSON.stringify(next)!==JSON.stringify(startOrder))changed(next);
    startOrder=null;
  };
  const begin=item=>{moving=item;startOrder=order();item.classList.add('widget-moving');};
  const move=(target,x,y)=>{
    if(!moving||!target||target===moving||target.parentElement!==grid)return;
    const rect=target.getBoundingClientRect(),source=moving.getBoundingClientRect();
    const sameRow=Math.abs(source.top-rect.top)<Math.min(source.height,rect.height)/2;
    const after=sameRow?x>rect.left+rect.width/2:y>rect.top+rect.height/2;
    if(touchId!==null){grid.insertBefore(moving,after?target.nextSibling:target);return;}
    dropTarget?.target.classList.remove('drop-target');dropTarget={target,after};target.classList.add('drop-target');
  };
  const dragstart=e=>{
    const handle=e.target.closest('[data-move-widget]');
    if(!unlocked||!handle){e.preventDefault();return;}
    begin(handle.closest('[data-widget-id]'));e.dataTransfer.effectAllowed='move';
    e.dataTransfer.setData('text/plain',moving.dataset.widgetId);
  };
  const dragover=e=>{if(moving){e.preventDefault();e.dataTransfer.dropEffect='move';move(e.target.closest('[data-widget-id]'),e.clientX,e.clientY);}};
  const drop=e=>{if(moving){e.preventDefault();finish();}};
  const pointerdown=e=>{
    if(e.pointerType==='mouse'||!unlocked)return;
    const handle=e.target.closest('[data-move-widget]');if(!handle)return;
    e.preventDefault();begin(handle.closest('[data-widget-id]'));touchId=e.pointerId;handle.setPointerCapture(e.pointerId);
  };
  const pointermove=e=>{if(touchId===e.pointerId){e.preventDefault();move(document.elementFromPoint(e.clientX,e.clientY)?.closest('[data-widget-id]'),e.clientX,e.clientY);}};
  const pointerend=e=>{if(touchId===e.pointerId)finish();};
  const keydown=e=>{
    if(!unlocked||!e.target.closest('[data-move-widget]')||!['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.key))return;
    e.preventDefault();const item=e.target.closest('[data-widget-id]'),list=items(),index=list.indexOf(item);
    const step=['ArrowUp','ArrowLeft'].includes(e.key)?-1:1,target=list[index+step];if(!target)return;
    grid.insertBefore(item,step<0?target:target.nextSibling);e.target.focus();changed(order());
  };
  const dragend=e=>{if(e.dataTransfer?.dropEffect==='none'){dropTarget?.target.classList.remove('drop-target');dropTarget=null;}finish();};
  const events={dragstart,dragover,drop,dragend,pointerdown,pointermove,pointerup:pointerend,pointercancel:pointerend,keydown};
  for(const [type,handler] of Object.entries(events))grid.addEventListener(type,handler);
  const refresh=()=>{
    grid.classList.toggle('layout-unlocked',unlocked);
    for(const handle of grid.querySelectorAll('[data-move-widget]')){handle.hidden=!unlocked;handle.draggable=unlocked;}
    for(const button of grid.querySelectorAll('.widget-toolbar [data-configure],.widget-toolbar [data-remove-widget]'))button.hidden=!unlocked;
  };
  return {refresh,lock(value){finish();unlocked=!value;refresh();},finish,destroy(){finish();for(const [type,handler] of Object.entries(events))grid.removeEventListener(type,handler);}};
}
