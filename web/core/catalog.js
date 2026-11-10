import {tText,tHtml,locale} from "./i18n.js";
import {$,esc} from './ui.js';
import './catalog.css';

// Keep disclosure forms mounted while filtering so unsaved access edits survive.
export function catalog(container,{items,search,label,filters=[],view={}}){
  container.classList.add('compact-catalog');
  const toolbar=document.createElement('div');toolbar.className='catalog-toolbar';
  toolbar.innerHTML=`<input type="search" aria-label="${esc(label)}" placeholder="${esc(label)}">${filters.length?`<select aria-label="${tHtml("common:filter", {value0:(esc(label))})}"><option value="">${tHtml("common:all")}</option>${filters.map(f=>`<option value="${esc(f.value)}">${esc(f.label)}</option>`).join('')}</select>`:''}`;
  const footer=document.createElement('div');footer.className='catalog-pagination';
  footer.innerHTML=`<span role="status"></span><div><button type="button" class="secondary compact" aria-label="${tHtml("common:previousPage", {value0:(esc(label))})}">←</button><button type="button" class="secondary compact" aria-label="${tHtml("common:nextPage", {value0:(esc(label))})}">→</button></div>`;
  const heading=container.querySelector(':scope>h2');if(heading)heading.after(toolbar);else container.prepend(toolbar);container.append(footer);
  const buttons=footer.querySelectorAll('button');
  $('input',toolbar).value=view.query||'';if($('select',toolbar))$('select',toolbar).value=view.filter||'';
  let page=view.page||0;
  function render(){
    const q=$('input',toolbar).value.trim().toLocaleLowerCase(locale()),filter=$('select',toolbar)?.value;
    const visible=items.filter(item=>search(item).toLocaleLowerCase(locale()).includes(q)&&(!filter||item.dataset.catalogFilter===filter));
    page=Math.min(page,Math.max(0,Math.ceil(visible.length/10)-1));const start=page*10;
    Object.assign(view,{query:$('input',toolbar).value,filter:filter||'',page});
    const shown=new Set(visible.slice(start,start+10));items.forEach(item=>item.hidden=!shown.has(item));
    $('[role=status]',footer).textContent=visible.length?tText("common:of", {value0:(start+1), value1:(Math.min(start+10,visible.length)), value2:(visible.length)}):tText("common:noResults");
    buttons[0].disabled=page===0;buttons[1].disabled=start+10>=visible.length;
  }
  $('input',toolbar).oninput=()=>{page=0;render();};
  if($('select',toolbar))$('select',toolbar).onchange=()=>{page=0;render();};
  buttons[0].onclick=()=>{page--;render();};buttons[1].onclick=()=>{page++;render();};render();
}

export function labelTableRows(table){
  const headings=[...table.querySelectorAll('thead th')].map(th=>th.textContent.trim());
  table.querySelectorAll('tbody tr').forEach(row=>[...row.cells].forEach((cell,i)=>{if(cell.colSpan===1)cell.dataset.label=headings[i];}));
  table.classList.add('responsive-table');table.setAttribute('role','table');
  table.querySelectorAll('tbody tr').forEach(row=>row.classList.toggle('has-selection',!!row.cells[0]?.querySelector('input[type=checkbox]')));
}
