import {tText} from "../../core/i18n.js";
const noteId=/^[A-Za-z0-9_-]{16}$/;
const fieldId=/^[A-Za-z0-9_-]{1,40}$/;
const propertyTypes=new Set(['text','number','checkbox','select','multi_select','date','url','formula','relation','rollup','dependency']);
// Mirror JSON transport's omitted optional fields before asking the device to
// sign. Keep nonfinite numbers invalid instead of silently turning them null.
export function databasePayload(value){
  if(value===undefined)return undefined;
  if(value===null||typeof value==='string'||typeof value==='boolean')return value;
  if(typeof value==='number'){if(Number.isFinite(value))return value;throw new Error(tText("federation:theDatabasePropertyContainsAnInvalidNumber"));}
  if(Array.isArray(value))return value.map(item=>item===undefined?null:databasePayload(item));
  if(value&&Object.getPrototypeOf(value)===Object.prototype)return Object.fromEntries(Object.entries(value).filter(([,item])=>item!==undefined).map(([key,item])=>[key,databasePayload(item)]));
  throw new Error(tText("federation:theDatabasePropertyContainsUnsupportedData"));
}
export function validateDatabasePayload(value,{catalog=false,limited=false}={}){
  const invalid=()=>{throw new Error(tText("federation:theFriendSServerReturnedAnInvalidDatabase"));};
  const object=item=>item!==null&&typeof item==='object'&&!Array.isArray(item)&&Object.getPrototypeOf(item)===Object.prototype;
  const text=(item,max)=>typeof item==='string'&&item.length<=max;
  const optional=(item,key,check)=>{if(item[key]!==undefined&&!check(item[key]))invalid();};
  const nullableId=(item,pattern)=>item===null||typeof item==='string'&&pattern.test(item);
  const boolean=item=>typeof item==='boolean';
  const role=item=>['editor','commenter','viewer'].includes(item);
  const list=(item,max,check)=>Array.isArray(item)&&item.length<=max&&item.every(check);
  const version=item=>Number.isSafeInteger(item)&&item>=1;
  const imageUrl=item=>{if(item===null)return true;if(!text(item,8192))return false;try{const url=new URL(item,'https://renderer.invalid');return ['http:','https:'].includes(url.protocol)&&!url.username&&!url.password&&(url.origin!=='https://renderer.invalid'||/^\/api\/(?:notes\/[A-Za-z0-9_-]+\/(?:assets|files)\/[A-Za-z0-9_-]+|media\/[A-Za-z0-9_-]+)(?:$|[?#])/.test(item));}catch{return false;}};
  const properties=items=>{if(!Array.isArray(items)||items.length>30)invalid();const seen=new Set();for(const item of items){if(!object(item)||typeof item.id!=='string'||!fieldId.test(item.id)||seen.has(item.id)||!propertyTypes.has(item.type)||!text(item.name,80))invalid();seen.add(item.id);
    for(const key of ['database_id'])optional(item,key,v=>nullableId(v,noteId));
    for(const key of ['inverse_id','relation_id','property'])optional(item,key,v=>nullableId(v,fieldId));
    for(const key of ['multiple','two_way','unavailable'])optional(item,key,boolean);
    optional(item,'options',v=>list(v,50,x=>text(x,80)));
    optional(item,'expression',v=>text(v,2000));
    optional(item,'aggregate',v=>['count','count_values','sum','average','min','max','unique','show_original','percent_checked','earliest','latest'].includes(v));
  }};
  if(catalog){if(!Array.isArray(value)||value.length>10000)invalid();for(const item of value){if(!object(item)||typeof item.id!=='string'||!noteId.test(item.id)||!text(item.title,200))invalid();properties(item.properties);}return value;}
  if(!object(value)||!limited&&(typeof value.note_id!=='string'||!noteId.test(value.note_id)))invalid();properties(value.properties);optional(value,'title',v=>text(v,200));
  optional(value,'role',role);optional(value,'schema_editable',boolean);optional(value,'version',version);optional(value,'note_version',version);
  if(!limited){if(!Array.isArray(value.views)||!value.views.length||value.views.length>20)invalid();const seen=new Set();for(const view of value.views){if(!object(view)||typeof view.id!=='string'||!fieldId.test(view.id)||seen.has(view.id)||!text(view.name,80)||!['table','list','board','calendar','gallery','timeline'].includes(view.type))invalid();seen.add(view.id);
    for(const key of ['group_by','date_by','dependency_by'])optional(view,key,v=>nullableId(v,fieldId));
    for(const key of ['auto_shift','unavailable_filter'])optional(view,key,boolean);
    optional(view,'lag_days',v=>Number.isSafeInteger(v)&&v>=-365&&v<=365);
    optional(view,'visible',v=>list(v,30,x=>typeof x==='string'&&fieldId.test(x)));
    optional(view,'filter',v=>v===null||object(v)&&typeof v.property==='string'&&fieldId.test(v.property)&&['contains','not_contains','equals','not_equals','empty','not_empty'].includes(v.operator)&&text(v.value,200));
    optional(view,'sort',v=>v===null||object(v)&&typeof v.property==='string'&&fieldId.test(v.property)&&['asc','desc'].includes(v.direction));
  }}
  const displayValue=(item,depth=0)=>depth<=20&&(item===null||text(item,200000)||typeof item==='boolean'||typeof item==='number'&&Number.isFinite(item)||Array.isArray(item)&&item.length<=2000&&item.every(x=>displayValue(x,depth+1))||object(item)&&Object.keys(item).length<=30&&Object.values(item).every(x=>displayValue(x,depth+1)));
  const rows=limited?[value.record]:value.records;if(!Array.isArray(rows)||rows.length>2000)invalid();for(const row of rows){if(!object(row)||typeof row.note_id!=='string'||!noteId.test(row.note_id)||!text(row.title,200)||!object(row.properties)||Object.keys(row.properties).length>30)invalid();
    for(const [key,item] of Object.entries(row.properties)){if(!fieldId.test(key)||!displayValue(item))invalid();const property=value.properties.find(p=>p.id===key);if(item===null)continue;
      if(['relation','dependency'].includes(property?.type)&&!list(item,100,id=>typeof id==='string'&&noteId.test(id)))invalid();
      if(['text','url'].includes(property?.type)&&!text(item,2000)||property?.type==='number'&&(typeof item!=='number'||!Number.isFinite(item))||property?.type==='checkbox'&&!boolean(item)||property?.type==='select'&&!text(item,80)||property?.type==='multi_select'&&!list(item,50,x=>text(x,80)))invalid();
      if(property?.type==='date'&&(!object(item)||Object.keys(item).some(key=>!['start','end'].includes(key))||!/^\d{4}-\d{2}-\d{2}$/.test(item.start)||typeof item.start!=='string'||item.end!=null&&(typeof item.end!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(item.end))))invalid();
    }
    optional(row,'role',role);optional(row,'version',version);optional(row,'icon',v=>v===null||text(v,100));
    for(const key of ['icon_url','cover_url','preview_url'])optional(row,key,imageUrl);
    optional(row,'computed_errors',v=>object(v)&&Object.entries(v).every(([key,item])=>fieldId.test(key)&&text(item,2000)));
  }
  optional(value,'relation_options',object);
  for(const [key,options] of Object.entries(value.relation_options||{})){if(!fieldId.test(key)||!object(options)||!Array.isArray(options.records)||options.records.length>2000||options.database_id!=null&&(typeof options.database_id!=='string'||!noteId.test(options.database_id)))invalid();optional(options,'unavailable',boolean);for(const record of options.records)if(!object(record)||typeof record.id!=='string'||!noteId.test(record.id)||!text(record.title,200))invalid();}
  return value;
}
// The existing database UI keeps its normal endpoint contract. Every remote
// action is translated to a device-signed intent; there is no local fallback.
export function databaseIntent(path,method='GET',data={},resource=()=>null){
  const scoped=id=>{if(!noteId.test(id))throw new Error(tText("federation:invalidDatabaseAddress"));const entry=resource(id);if(!Number.isSafeInteger(entry?.revision)||entry.revision<1)throw new Error(tText("federation:updateSharedNotesAccessToThisDatabaseHas"));return {note_id:id,revision:entry.revision};};
  if(path==='/database-catalog'&&method==='GET')return {operation:'database_catalog',payload:{}};
  let match=/^\/databases\/([A-Za-z0-9_-]{16})(?:\/(records|formula-preview))?$/.exec(path);
  if(match){const base=scoped(match[1]);if(!match[2]&&method==='GET')return {operation:'database_read',payload:base};if(!match[2]&&method==='PUT')return {operation:'database_configure',payload:{...base,version:data.version,properties:data.properties,views:data.views}};if(match[2]==='records'&&method==='POST')return {operation:'database_record_create',payload:{...base,title:data.title,properties:data.properties}};if(match[2]==='formula-preview'&&method==='POST')return {operation:'database_formula_preview',payload:{...base,expression:data.expression,...data.record_id?{record_id:data.record_id}:{}}};}
  match=/^\/database-records\/([A-Za-z0-9_-]{16})$/.exec(path);if(match&&method==='PATCH')return {operation:'database_record_update',payload:{...scoped(match[1]),version:data.version,properties:data.properties}};
  match=/^\/notes\/([A-Za-z0-9_-]{16})\/database-title$/.exec(path);if(match&&method==='PATCH')return {operation:'database_title',payload:{...scoped(match[1]),version:data.version,title:data.title}};
  if(path==='/databases'&&method==='POST'){const parent=scoped(data.parent_id);return {operation:'database_create',payload:{parent_id:parent.note_id,revision:parent.revision,title:data.title}};}
  throw new Error(tText("federation:thisActionIsNotAvailableForAShared"));
}
