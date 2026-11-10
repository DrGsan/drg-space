import {tText} from '../../core/i18n.js';
import {$,esc} from '../../core/ui.js';

export function pagePicker(form,ctx,dialog){
  const trigger=$('[data-source-picker]',form),panel=$('[data-source-options]',form),input=$('[data-source-search]',form),list=$('[data-source-list]',form),status=$('[data-source-status]',form),more=$('[data-source-more]',form),source=$('[name=source]',form),connection=$('[name=connection]',form),preview=$('[type=submit]',form);
  let turn=0,items=[],limit=100,loaded=false,expanded=new Set();
  const refresh=document.createElement('button');refresh.type='button';refresh.className='text-link compact notion-tree-refresh';refresh.textContent=tText('portability:notionTreeRefresh');panel.append(refresh);
  const hint=document.createElement('p');hint.className='hint notion-tree-hint';hint.textContent=tText('portability:notionTreeHint');panel.append(hint);
  function choose(page){
    if(page.linked)return;
    source.value='https://www.notion.so/'+page.id;trigger.textContent=(page.emoji||'')+' '+page.title;preview.disabled=false;panel.hidden=true;trigger.setAttribute('aria-expanded','false');trigger.focus();
  }
  function render(focus){
    const query=input.value.trim().toLocaleLowerCase(),children=new Map();
    for(const item of items){const parent=item.parent_id||'';if(!children.has(parent))children.set(parent,[]);children.get(parent).push(item);}
    list.replaceChildren();list.setAttribute('role',query?'listbox':'tree');
    let count=0;
    function row(page,level){
      const node=document.createElement('div');node.className='notion-tree-row';node.dataset.pageId=page.id;node.style.setProperty('--level',Math.min(level,12));node.tabIndex=count===0?0:-1;node.onfocus=()=>{for(const row of list.children)row.tabIndex=row===node?0:-1;};node.setAttribute('role',query?'option':'treeitem');
      node.setAttribute('aria-selected',String(source.value.endsWith(page.id)));if(!query){node.setAttribute('aria-level',level+1);if(page.has_children)node.setAttribute('aria-expanded',String(expanded.has(page.id)));}
      if(page.linked)node.setAttribute('aria-disabled','true');
      const toggle=document.createElement('button');toggle.type='button';toggle.className='notion-tree-toggle';toggle.tabIndex=-1;
      if(!query&&page.has_children){toggle.textContent=expanded.has(page.id)?'⌄':'›';toggle.setAttribute('aria-label',tText(expanded.has(page.id)?'portability:notionTreeCollapse':'portability:notionTreeExpand',{title:page.title}));toggle.onclick=()=>{expanded.has(page.id)?expanded.delete(page.id):expanded.add(page.id);render(page.id);};}else{toggle.hidden=true;}
      const select=document.createElement('button');select.type='button';select.className='notion-tree-select';select.tabIndex=-1;select.disabled=!!page.linked;
      const reason=page.linked?tText('portability:'+(page.linked.relation==='contains'?'notionTreeContains':page.linked.relation==='reserved'?'notionTreeReserved':'notionTreeIncluded'),{title:page.linked.title}):'';
      select.innerHTML=`<span aria-hidden="true">${esc(page.emoji||(page.kind==='database'?'▦':'📄'))}</span><span><span class="notion-tree-title">${esc(page.title)}</span>${query&&page.path.length?`<small>${esc(page.path.join(' › '))}</small>`:''}${reason?`<small class="notion-tree-linked">${esc(reason)}</small>`:''}</span>`;
      select.onclick=()=>choose(page);node.append(toggle,select);list.append(node);count++;
      node.onkeydown=event=>{
        const nodes=[...list.querySelectorAll('[data-page-id]')],index=nodes.indexOf(node);
        if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();nodes[index+(event.key==='ArrowDown'?1:-1)]?.focus();}
        if(event.key==='Home'){event.preventDefault();nodes[0]?.focus();}
        if(event.key==='End'){event.preventDefault();nodes.at(-1)?.focus();}
        if(event.key==='Enter'||event.key===' '){event.preventDefault();choose(page);}
        if(!query&&event.key==='ArrowRight'&&page.has_children){event.preventDefault();if(!expanded.has(page.id)){expanded.add(page.id);render(page.id);}else nodes[index+1]?.focus();}
        if(!query&&event.key==='ArrowLeft'){event.preventDefault();if(expanded.delete(page.id))render(page.id);else if(page.parent_id)list.querySelector(`[data-page-id="${page.parent_id}"]`)?.focus();}
      };
    }
    const shown=new Set();
    function walk(parent,level){for(const page of children.get(parent)||[]){if(shown.has(page.id))continue;shown.add(page.id);row(page,level);if(expanded.has(page.id))walk(page.id,level+1);}}
    if(query){const matches=items.filter(p=>p.title.toLocaleLowerCase().includes(query));for(const page of matches.slice(0,limit))row(page,0);more.hidden=matches.length<=limit;}
    else{walk('',0);more.hidden=true;}
    status.textContent=count?'':tText('portability:notionPagesEmpty');
    if(focus)list.querySelector(`[data-page-id="${focus}"]`)?.focus();
  }
  async function load(force=false){
    const generation=++turn;status.textContent=tText('portability:notionPagesLoading');refresh.disabled=true;
    try{const result=await ctx.api('/account/notion-sync/tree','POST',{connection_id:connection.value,refresh:force});if(generation!==turn||!form.isConnected)return;items=result.items;loaded=true;render();}
    catch(error){if(generation===turn&&form.isConnected)status.textContent=error.message;}
    finally{if(generation===turn)refresh.disabled=false;}
  }
  trigger.onclick=()=>{panel.hidden=!panel.hidden;trigger.setAttribute('aria-expanded',String(!panel.hidden));if(!panel.hidden){input.focus();load();}};
  input.oninput=()=>{limit=100;if(loaded)render();};
  panel.onkeydown=event=>{if(event.key==='Escape'){panel.hidden=true;trigger.setAttribute('aria-expanded','false');trigger.focus();}};
  more.onclick=()=>{limit+=100;render();};refresh.onclick=()=>load(true);
  connection.onchange=()=>{turn++;items=[];expanded.clear();loaded=false;source.value='';input.value='';trigger.textContent=tText('portability:notionChoosePage');preview.disabled=true;panel.hidden=true;trigger.setAttribute('aria-expanded','false');};
  source.oninput=()=>{preview.disabled=!source.value.trim();trigger.textContent=tText('portability:notionChoosePage');};
  dialog.addEventListener('close',()=>{turn++;},{once:true});
}
