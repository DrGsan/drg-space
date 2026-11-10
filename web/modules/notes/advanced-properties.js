import {tText,tHtml} from "../../core/i18n.js";
import {$,esc} from '../../core/ui.js';
const aggregates={count:tText("notes:numberOfRelatedRecords"),count_values:tText("notes:numberOfNonEmpty"),sum:tText("notes:sum"),average:tText("notes:average"),min:tText("notes:minimum"),max:tText("notes:maximum"),unique:tText("notes:uniqueValues"),show_original:tText("notes:allValues"),percent_checked:tText("notes:percentageMarked"),earliest:tText("notes:earliestDate"),latest:tText("notes:latestDate")};
export async function propertiesEditor(db,ctx,save,labels){
  const draft=structuredClone(db.properties),version=db.version;
  let catalog=[];
  try{catalog=(await ctx.api('/database-catalog')).filter(x=>x.space_id===(ctx.state.notes.find(n=>n.id===db.note_id)?.space_id||null));}catch(e){ctx.toast(e.message);}
  const el=ctx.modal(`<h2>${tHtml("notes:databaseProperties")}</h2><p class=hint>${tHtml("notes:formulasAndRollupsAreCalculatedAutomaticallyRelationshipsAre")}</p><div id=db-properties></div><button class=secondary id=db-add-property>${tHtml("notes:property")}</button><button class=primary id=db-save-properties>${tHtml("notes:saveBaseProperties")}</button><p class=error role=alert></p>`);
  const options=(selected)=>catalog.map(d=>`<option value="${d.id}" ${d.id===selected?'selected':''}>${esc(d.title)}</option>`).join('');
  function advanced(p,i){
    if(p.unavailable)return `<p class="hint">${tHtml("notes:theLinkedBaseIsUnavailableItsSettingsWill")}</p>`;
    if(p.type==='formula')return `<label>${tHtml("notes:formula")}<textarea data-formula maxlength=2000 rows=3 placeholder='${tHtml("notes:propPricePropQuantity")}'>${esc(p.expression||'')}</textarea></label><p class=hint>${tHtml("notes:propNameIfRoundSumLengthConcatEmpty")}</p><button class="text-link" data-preview-formula type=button>${tHtml("notes:checkOnTheFirstRecord")}</button><p class=hint data-formula-result></p>`;
    if(p.type==='relation')return `<label>${tHtml("notes:linkedBase")}<select data-related-database><option value="">${tHtml("notes:selectBase")}</option>${options(p.database_id)}</select></label><label class=check-label><input data-related-multiple type=checkbox ${p.multiple!==false?'checked':''}>${tHtml("notes:multipleRecordsCanBeSelected")}</label><label class=check-label><input data-related-two-way type=checkbox ${p.two_way?'checked':''}>${tHtml("notes:twoWayLinkCreateAReverseProperty")}</label>`;
    if(p.type==='rollup'){
      const relations=draft.filter(x=>x.type==='relation'),rel=relations.find(x=>x.id===p.relation_id),target=catalog.find(d=>d.id===rel?.database_id);
      return `<label>${tHtml("notes:relation")}<select data-rollup-relation><option value="">${tHtml("notes:selectLink")}</option>${relations.map(x=>`<option value="${x.id}" ${x.id===p.relation_id?'selected':''}>${esc(x.name)}</option>`).join('')}</select></label><label>${tHtml("notes:propertyOfTheLinkedRecord")}<select data-rollup-property><option value=title ${p.property==='title'?'selected':''}>${tHtml("notes:name")}</option>${(target?.properties||[]).map(x=>`<option value="${x.id}" ${x.id===p.property?'selected':''}>${esc(x.name)}</option>`).join('')}</select></label><label>${tHtml("notes:calculate")}<select data-rollup-aggregate>${Object.entries(aggregates).map(([key,label])=>`<option value="${key}" ${key===(p.aggregate||'count')?'selected':''}>${label}</option>`).join('')}</select></label>`;
    }
    if(p.type==='dependency')return `<p class=hint>${tHtml("notes:selectsPrecedingTasksInThisBaseIncludeArrows")}</p>`;
    return '';
  }
  function draw(){
    const box=$('#db-properties',el);box.innerHTML=draft.map((p,i)=>`<div class=db-property-card data-prop-index="${i}"><div class=db-property-editor><input aria-label="${tHtml("notes:propertyName", {value0:(i+1)})}" maxlength=80 value="${esc(p.name)}" data-prop-name><select aria-label="${tHtml("notes:propertyType", {value0:(i+1)})}" data-prop-type>${Object.entries(labels).map(([type,label])=>`<option value="${type}" ${p.type===type?'selected':''}>${label}</option>`).join('')}</select><button class=icon-btn data-remove="${i}" aria-label="${tHtml("notes:deleteProperty", {value0:(esc(p.name))})}">×</button><input aria-label="${tHtml("notes:propertyOptions", {value0:(i+1)})}" data-prop-options placeholder="${tHtml("notes:optionsSeparatedByCommas")}" value="${esc((p.options||[]).join(', '))}" ${['select','multi_select'].includes(p.type)?'':'hidden'}></div>${advanced(p,i)}</div>`).join('');
    box.querySelectorAll('[data-prop-index]').forEach(row=>{
      const p=draft[Number(row.dataset.propIndex)];row.querySelector('[data-prop-name]').oninput=e=>p.name=e.target.value;
      if(p.unavailable){row.querySelectorAll('input,select,button').forEach(input=>input.disabled=true);return;}
      row.querySelector('[data-prop-type]').onchange=e=>{p.type=e.target.value;draw();};row.querySelector('[data-prop-options]').oninput=e=>p.options=e.target.value.split(',').map(x=>x.trim()).filter(Boolean);
      row.querySelector('[data-formula]')?.addEventListener('input',e=>p.expression=e.target.value);
      row.querySelector('[data-preview-formula]')?.addEventListener('click',async()=>{try{const result=await ctx.api('/databases/'+db.note_id+'/formula-preview','POST',{expression:p.expression||''});row.querySelector('[data-formula-result]').textContent=result.error||tText("notes:result", {value0:(JSON.stringify(result.value))});}catch(e){row.querySelector('[data-formula-result]').textContent=e.message;}});
      row.querySelector('[data-related-database]')?.addEventListener('change',e=>{p.database_id=e.target.value;delete p.inverse_id;});
      row.querySelector('[data-related-multiple]')?.addEventListener('change',e=>p.multiple=e.target.checked);
      row.querySelector('[data-related-two-way]')?.addEventListener('change',e=>p.two_way=e.target.checked);
      row.querySelector('[data-rollup-relation]')?.addEventListener('change',e=>{p.relation_id=e.target.value;p.property='title';draw();});
      row.querySelector('[data-rollup-property]')?.addEventListener('change',e=>p.property=e.target.value);
      row.querySelector('[data-rollup-aggregate]')?.addEventListener('change',e=>p.aggregate=e.target.value);
    });
    box.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{if(confirm(tText("notes:deletePropertyAndItsValues", {value0:(draft[Number(b.dataset.remove)].name)}))){draft.splice(Number(b.dataset.remove),1);draw();}});
  }
  $('#db-add-property',el).onclick=()=>{if(draft.length>=30)return;draft.push({id:'p_'+crypto.randomUUID().replaceAll('-','').slice(0,12),name:tText("notes:newProperty"),type:'text',options:[]});draw();};
  $('#db-save-properties',el).onclick=async()=>{
    try{
      for(const p of draft)if(p.type==='rollup'&&!p.unavailable){p.aggregate||='count';p.property||='title';}
      const ids=new Set(draft.map(p=>p.id));const views=db.views.map(v=>({...v,visible:(v.visible||[]).filter(x=>ids.has(x)),group_by:draft.some(p=>p.id===v.group_by&&p.type==='select')?v.group_by:null,date_by:draft.some(p=>p.id===v.date_by&&p.type==='date')?v.date_by:null,dependency_by:draft.some(p=>p.id===v.dependency_by&&p.type==='dependency')?v.dependency_by:null,filter:v.filter&&v.filter.property!=='title'&&!ids.has(v.filter.property)?null:v.filter,sort:v.sort&&v.sort.property!=='title'&&!ids.has(v.sort.property)?null:v.sort}));
      await save(draft,views,version);$('.modal-close',el).click();
    }catch(e){$('.error',el).textContent=e.message;}
  };
  draw();
}
export function dependencyArrows(host,rows,property){
  if(!property)return;
  const tracks=host.querySelector('.db-timeline');if(!tracks)return;
  const bounds=tracks.getBoundingClientRect();let paths=[];
  for(const row of rows){const end=tracks.querySelector(`[data-record-id="${row.note_id}"] .db-timeline-bar`);if(!end)continue;const b=end.getBoundingClientRect();for(const id of row.properties[property]||[]){const start=tracks.querySelector(`[data-record-id="${id}"] .db-timeline-bar`);if(!start)continue;const a=start.getBoundingClientRect(),x1=a.right-bounds.left,y1=a.top+a.height/2-bounds.top,x2=b.left-bounds.left,y2=b.top+b.height/2-bounds.top,middle=x1+Math.max(10,(x2-x1)/2);paths.push(`<path d="M ${x1} ${y1} H ${middle} V ${y2} H ${x2}" marker-end="url(#dependency-arrow)"><title>${tHtml("notes:taskDependency", {value0:(esc(row.title))})}</title></path>`);}}
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('db-dependency-arrows');svg.setAttribute('width',tracks.scrollWidth);svg.setAttribute('height',tracks.scrollHeight);svg.innerHTML='<defs><marker id=dependency-arrow viewBox="0 0 10 10" refX=9 refY=5 markerWidth=6 markerHeight=6 orient=auto><path d="M 0 0 L 10 5 L 0 10 z"/></marker></defs>'+paths.join('');tracks.append(svg);
}
