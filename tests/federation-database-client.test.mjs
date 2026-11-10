import test from 'node:test';
import assert from 'node:assert/strict';
import {databaseIntent,databasePayload,validateDatabasePayload} from '../web/modules/federation/database-protocol.js';
import {attachmentOrigins} from '../web/modules/notes/import-markdown.js';

const db='D'.repeat(16),record='R'.repeat(16),revision=7,resource=id=>[db,record].includes(id)?{revision}:null;
test('existing native database endpoints map to grant-bound signed operations',()=>{
  const map=(path,method,data)=>databaseIntent(path,method,data,resource),base={note_id:db,revision};
  assert.deepEqual(map('/database-catalog','GET'),{operation:'database_catalog',payload:{}});
  assert.deepEqual(map('/databases/'+db,'GET'),{operation:'database_read',payload:base});
  const settings={version:3,properties:[{id:'price',type:'number'}],views:[{id:'table',type:'table'}]};
  assert.deepEqual(map('/databases/'+db,'PUT',settings),{operation:'database_configure',payload:{...base,...settings}});
  assert.deepEqual(map('/databases/'+db+'/records','POST',{title:'Запись',properties:{price:12.5}}),{operation:'database_record_create',payload:{...base,title:'Запись',properties:{price:12.5}}});
  assert.deepEqual(map('/database-records/'+record,'PATCH',{version:4,properties:{price:15}}),{operation:'database_record_update',payload:{note_id:record,revision,version:4,properties:{price:15}}});
  assert.deepEqual(map('/notes/'+db+'/database-title','PATCH',{version:9,title:'База'}),{operation:'database_title',payload:{...base,version:9,title:'База'}});
  assert.deepEqual(map('/databases/'+db+'/formula-preview','POST',{expression:'prop("price")*2',record_id:record}),{operation:'database_formula_preview',payload:{...base,expression:'prop("price")*2',record_id:record}});
  assert.deepEqual(map('/databases','POST',{parent_id:db,title:'Вложенная база',space_id:'local-space'}),{operation:'database_create',payload:{parent_id:db,revision,title:'Вложенная база'}});
});
test('remote adapters never fall back to local APIs or guess missing grant revisions',()=>{
  for(const path of ['/databases/'+db+'?user=other','/admin/users','https://other.example/databases/'+db,'/notes/'+db+'/database-title/extra','/database-records/short'])assert.throws(()=>databaseIntent(path,'GET',{},resource));
  assert.throws(()=>databaseIntent('/databases/'+db,'GET',{},()=>null));
  assert.throws(()=>databaseIntent('/databases','POST',{title:'Unscoped root'},resource));
  assert.throws(()=>databaseIntent('/databases/'+db,'DELETE',{},resource));
});
test('untrusted remote IDs cannot become HTML attributes or selector fragments in the native database UI',()=>{
  const valid={note_id:db,title:'<img src=x onerror=alert(1)>',properties:[{id:'price',name:'<script>bad()</script>',type:'number'}],views:[{id:'table',name:'<svg/onload=alert(1)>',type:'table'}],records:[{note_id:record,title:'<img onerror=alert(1)>',properties:{price:5}}],relation_options:{}};
  assert.equal(validateDatabasePayload(valid),valid);
  for(const malicious of [{...valid,note_id:'x" onclick="bad()'},{...valid,properties:[{id:'x" onload="bad()',name:'field',type:'text'}]},{...valid,views:[{id:'x"] body',name:'table',type:'table'}]},{...valid,records:[{note_id:'x" onclick="bad()',title:'record',properties:{}}]},{...valid,relation_options:{related:{records:[{id:'x" onclick="bad()',title:'record'}]}}}])assert.throws(()=>validateDatabasePayload(malicious));
});
test('legacy attachment conversion binds relative app assets to signed web origin and preserves absolute links',()=>{
  const web='https://owner.example',absolute='https://another.example/api/notes/'+db+'/files/'+record;
  const input=[{type:'image',props:{url:'/api/notes/'+db+'/files/'+record}},{type:'paragraph',content:[{type:'link',href:'/api/media/'+'a'.repeat(32)}]},{type:'image',props:{url:absolute}},{type:'paragraph',content:[{type:'link',href:'/manual'}]}],converted=attachmentOrigins(input,web);
  assert.equal(converted[0].props.url,web+input[0].props.url);assert.equal(converted[1].content[0].href,web+input[1].content[0].href);assert.equal(converted[2].props.url,absolute);assert.equal(converted[3].content[0].href,'/manual');assert.equal(input[0].props.url.startsWith('/api/'),true);
});
test('optional native view fields use JSON omission before signing without accepting nonfinite numbers',()=>{
  assert.deepEqual(databasePayload({views:[{id:'table',filter:undefined,sort:undefined}],number:12.5}),{views:[{id:'table'}],number:12.5});
  for(const number of [Infinity,-Infinity,NaN])assert.throws(()=>databasePayload({number}));
});
test('signed remote database data must satisfy every renderer field type before reaching native dialogs',()=>{
  const valid={note_id:db,title:'Shared',properties:[{id:'date',name:'Date',type:'date'},{id:'related',name:'Relation',type:'relation',database_id:null,unavailable:true}],views:[{id:'table',name:'Table',type:'table',lag_days:0,auto_shift:false,visible:['date'],filter:null,unavailable_filter:true,sort:{property:'title',direction:'asc'}}],records:[{note_id:record,title:'Record',properties:{date:{start:'2026-10-26'},related:[]},preview_url:'https://owner.example/api/notes/'+record+'/files/'+'f'.repeat(32)}],relation_options:{related:{database_id:null,records:[],unavailable:true}}};
  assert.equal(validateDatabasePayload(valid),valid);
  const view=patch=>({...valid,views:[{...valid.views[0],...patch}]}),property=patch=>({...valid,properties:[{...valid.properties[0],...patch}]}),row=patch=>({...valid,records:[{...valid.records[0],...patch}]});
  for(const malicious of [view({lag_days:'0"><img src=x onerror=alert(1)>'}),view({lag_days:366}),view({lag_days:0.5}),view({lag_days:NaN}),view({auto_shift:'false'}),view({visible:'date'}),view({filter:{property:'date',operator:'contains',value:{}}}),view({sort:{property:'title',direction:'<img>'}}),property({options:'<img>'}),property({expression:{}}),property({unavailable:'false'}),row({properties:{related:['x"] body']}}),row({preview_url:'javascript:alert(1)'}),row({preview_url:'/api/admin/users'}),row({computed_errors:{date:{message:'bad'}}})])assert.throws(()=>validateDatabasePayload(malicious));
});
