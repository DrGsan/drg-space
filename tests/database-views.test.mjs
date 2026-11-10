import {test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const bundle=await build({stdin:{contents:"export {matchesFilter,formatProperty,filterDefaults} from './web/modules/notes/database.js';",resolveDir:process.cwd()},bundle:true,write:false,format:'esm',loader:{'.css':'empty'}});
const {matchesFilter,formatProperty,filterDefaults}=await import('data:text/javascript;base64,'+Buffer.from(bundle.outputFiles[0].text).toString('base64'));
test('archive filters match complete tags and include empty tags in the active view',()=>{
 const filter={operator:'not_contains',value:'Архив'};
 for(const tags of [[],['IT'],['Неархив']])assert.equal(matchesFilter(tags,filter),true);
 for(const tags of [['Архив'],['IT','АРХИВ']])assert.equal(matchesFilter(tags,filter),false);
 assert.equal(matchesFilter(['IT','Архив'],{...filter,operator:'contains'}),true);
 assert.equal(matchesFilter('Some title',{operator:'contains',value:'TITLE'}),true);
});
test('new archive records inherit only a valid matching select option',()=>{
 const properties=[{id:'tags',type:'multi_select',options:['IT','Архив']}];
 assert.deepEqual(filterDefaults(properties,{property:'tags',operator:'contains',value:'архив'}),{tags:['Архив']});
 assert.deepEqual(filterDefaults(properties,{property:'tags',operator:'not_contains',value:'Архив'}),{});
 assert.deepEqual(filterDefaults(properties,{property:'tags',operator:'contains',value:'unknown'}),{});
});
test('Notion wall-clock timestamps retain their date and minute without timezone conversion',()=>{
 const prop={type:'text',name:'Last edited time'};
 assert.equal(formatProperty(prop,'February 18, 2022 4:02 PM'),'18/02/2022 16:02');
 assert.equal(formatProperty(prop,'January 1, 2026 12:00 AM'),'01/01/2026 00:00');
 assert.equal(formatProperty(prop,'January 1, 2026 12:00 PM'),'01/01/2026 12:00');
 assert.equal(formatProperty({type:'text',name:'Text'},'February 18, 2022 4:02 PM'),'February 18, 2022 4:02 PM');
});
