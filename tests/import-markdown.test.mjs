import {test} from 'node:test';
import assert from 'node:assert/strict';
import {importedFileBlocks} from '../web/modules/notes/import-markdown.js';

const origin='https://notes.example';
const link=(href,text='config.yaml')=>({type:'link',href,content:[{type:'text',text,styles:{}}]});
const paragraph=content=>({id:'attachment',type:'paragraph',content,children:[]});
test('private standalone attachment links become files recursively without losing their captions',()=>{
  const source=[{type:'toggleListItem',content:[],children:[paragraph([link(origin+'/api/notes/page/files/file')])]}];
  const result=importedFileBlocks(source,origin)[0].children[0];
  assert.equal(result.type,'file');assert.equal(result.id,'attachment');assert.equal(result.props.name,'config.yaml');
  assert.equal(result.props.url,origin+'/api/notes/page/files/file');
  assert.equal(source[0].children[0].type,'paragraph');
});
test('web links, code, inline references and signed URLs retain their original meaning',()=>{
  for(const href of ['https://other.example/api/notes/page/files/file','https://notes.example/manual.pdf','https://notes.example/api/notes/page/files/file?token=x']){
    assert.equal(importedFileBlocks([paragraph([link(href)])],origin)[0].type,'paragraph');
  }
  const href=origin+'/api/notes/page/files/file';
  assert.equal(importedFileBlocks([paragraph([{type:'text',text:'Read ',styles:{}},link(href)])],origin)[0].type,'paragraph');
  assert.equal(importedFileBlocks([{...paragraph([link(href)]),type:'codeBlock'}],origin)[0].type,'codeBlock');
});
