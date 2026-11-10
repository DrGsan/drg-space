import {test} from 'node:test';
import assert from 'node:assert/strict';
import {equationTextProjection,parseInlineEquations} from '../web/modules/notes/equation-markdown.js';
test('equation projection preserves rich atoms while exporting nested content and tables',()=>{
 const blocks=[{type:'paragraph',content:[{type:'equation',props:{latex:String.raw`\frac{a}{b}`}}],children:[{type:'table',content:{rows:[{cells:[[{type:'equation',props:{latex:'E=mc^2'}}]]}]}}]}];
 const original=structuredClone(blocks),copy=equationTextProjection(blocks);assert.deepEqual(blocks,original);assert.equal(copy[0].content[0].text,String.raw`$\frac{a}{b}$`);assert.equal(copy[0].children[0].content.rows[0].cells[0][0].text,'$E=mc^2$');
});
test('Markdown equations retain surrounding styles and leave code, escaped dollars and currency intact',()=>{
 const content=text=>[{type:'text',text,styles:{bold:true}}],blocks=[{type:'paragraph',content:content('До $E=mc^2$ после'),children:[]},{type:'codeBlock',content:content('$x$')},{type:'paragraph',content:[{type:'text',text:'$x$',styles:{code:true}}]},{type:'paragraph',content:content(String.raw`Цена $100 и $200. Экранировано \$x$`)}];
 const result=parseInlineEquations(blocks);assert.deepEqual(result[0].content.map(p=>p.type),['text','equation','text']);assert.equal(result[0].content[2].styles.bold,true);assert.equal(result[1].content[0].type,'text');assert.equal(result[2].content[0].type,'text');assert.equal(result[3].content.length,1);
});

test('math-like text inside a spoiler stays hidden text during Markdown import',()=>{
 const blocks=[{type:'paragraph',content:[{type:'text',text:'$secret$',styles:{spoiler:true}}]}];
 assert.deepEqual(parseInlineEquations(structuredClone(blocks)),blocks);
});
