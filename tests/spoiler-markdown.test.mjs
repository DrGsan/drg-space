import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseInlineSpoilers,spoilerMarkdown} from '../web/modules/notes/spoiler-markdown.js';
const t=(text,styles={})=>({type:'text',text,styles});
test('spoiler delimiters span bold, code and links without altering code blocks or unmatched text',()=>{
 const blocks=[{type:'paragraph',content:[t('До ||тайна '),t('жирно',{bold:true}),t(' code',{code:true}),{type:'link',href:'https://example.com',content:[t(' ссылка|| после')]}],children:[{type:'paragraph',content:[t('||вложено||')]}]},{type:'codeBlock',content:[t('||код||')]},{type:'paragraph',content:[t('||литерал||',{code:true}),t(' незакрыто ||')]}];
 const original=structuredClone(blocks),parsed=parseInlineSpoilers(blocks);
 assert.deepEqual(blocks,original);assert.deepEqual(parsed[0].content.slice(0,2),[t('До '),t('тайна ',{spoiler:true})]);
 assert.deepEqual(parsed[0].content[2],t('жирно',{bold:true,spoiler:true}));assert.equal(parsed[0].content[3].styles.code,true);assert.equal(parsed[0].content[3].styles.spoiler,true);
 assert.deepEqual(parsed[0].content[4].content,[t(' ссылка',{spoiler:true}),t(' после')]);assert.equal(parsed[0].children[0].content[0].styles.spoiler,true);
 assert.deepEqual(parsed[1],blocks[1]);assert.deepEqual(parsed[2],blocks[2]);
});
test('multiple hidden ranges, table cells and ordinary four pipes survive',()=>{
 const table={type:'table',content:{type:'tableContent',rows:[{cells:[[t('||один|| и ||два||')],{type:'tableCell',content:[t('||три||')]}]}]}};
 const parsed=parseInlineSpoilers([table]);assert.equal(parsed[0].content.rows[0].cells[0].filter(p=>p.styles.spoiler).length,2);assert.equal(parsed[0].content.rows[0].cells[1].content[0].styles.spoiler,true);
 const original=[{type:'paragraph',content:[t('||||'),t('secret',{code:true,spoiler:true})]}];
 const output=spoilerMarkdown(original,blocks=>blocks[0].content.map(p=>p.styles.code?'`'+p.text+'`':p.text).join(''));
 assert.equal(output,'||||||`secret`||');assert.equal(original[0].content[1].styles.spoiler,true);
});
