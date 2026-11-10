import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {codeHighlighter} from '../web/modules/notes/code.js';

const css=await readFile(new URL('../web/modules/notes/code-theme.css',import.meta.url),'utf8');
const backgrounds=[...css.matchAll(/--note-code-background:\s*(#[\da-f]{6})/gi)].map(match=>match[1]);
function luminance(hex){
  const rgb=hex.slice(1).match(/../g).map(value=>parseInt(value,16)/255);
  return rgb.map(value=>value<=.04045?value/12.92:((value+.055)/1.055)**2.4)
    .reduce((total,value,index)=>total+value*[.2126,.7152,.0722][index],0);
}
function contrast(a,b){const values=[luminance(a),luminance(b)].sort((a,b)=>b-a);return (values[0]+.05)/(values[1]+.05);}
const examples={
  python:'# Текст комментария\ndef greet(name):\n\treturn "Hello " + name\n',
  sql:'-- A comment\nSELECT id, name FROM users WHERE active = true AND age > 18;\n',
  bash:'# A comment\nexport PATH="/usr/local/bin:$PATH"\necho "$HOME"\n',
  javascript:'// A comment\nexport const greet = name => `Hello ${name}`;\n',
  typescript:'// A comment\nconst count: number = 12;\n',
  json:'{"city":"Москва","enabled":true,"count":12}\n',
  html:'<!-- A comment -->\n<section class="panel">Hello &amp; welcome</section>\n',
  css:'/* A comment */\n.panel { color: #123abc; display: grid; }\n',
  yaml:'# A comment\ncity: Москва\nenabled: true\n',
  markdown:'# A heading\n**Strong** and [link](https://example.com).\n',
  docker:'FROM python:3.14\nWORKDIR /app\nRUN python --version\n',
};
test('both code palettes keep comments and syntax readable on their actual surfaces',async()=>{
  assert.equal(backgrounds.length,2);
  const highlighter=await codeHighlighter();
  for(const [lang,source] of Object.entries(examples)){
    const result=highlighter.codeToTokens(source,{lang,defaultColor:false,themes:{light:'github-light-high-contrast',dark:'github-dark-high-contrast'}});
    assert.equal(result.tokens.map(line=>line.map(token=>token.content).join('')).join('\n'),source);
    for(const [index,scheme] of ['light','dark'].entries()){
      const colors=new Set();
      for(const line of result.tokens)for(const token of line){
        if(!token.content.trim())continue;
        const color=token.htmlStyle['--shiki-'+scheme];colors.add(color);
        assert.ok(contrast(color,backgrounds[index])>=4.5,`${lang}/${scheme}: ${color} on ${backgrounds[index]}`);
      }
      assert.ok(colors.size>1,`${lang}/${scheme} retains syntax highlighting`);
    }
  }
});
