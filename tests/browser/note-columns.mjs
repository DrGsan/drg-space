import {chromium,webkit,expect} from '@playwright/test';
import {build} from 'esbuild';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createServer} from 'node:http';
const dir=await mkdtemp(tmpdir()+'/drg62-columns-');let server,browser;
try{
 await build({stdin:{contents:`import {mountBlockEditor} from './web/modules/notes/blocks.js';import * as actions from './web/modules/notes/column-actions.js';import {beginBlockDrag} from './web/modules/notes/toggle-drop.js';window.columnActions=actions;window.beginBlockDrag=beginBlockDrag;window.mount=(blocks,readOnly=false)=>{window.mounted?.destroy();window.changes=0;window.mounted=mountBlockEditor(document.querySelector('#editor'),{id:'columns-check',blocks},{readOnly,onChange:()=>window.changes++});window.editor=window.mounted.editor;};`,resolveDir:process.cwd(),sourcefile:'columns-harness.js'},bundle:true,conditions:['style'],outdir:dir,format:'esm',entryNames:'harness',loader:{'.woff2':'file','.woff':'file','.ttf':'file','.svg':'file','.png':'file'},define:{'process.env.NODE_ENV':'"production"'}});
 const manifest=JSON.parse(await readFile('app/static/asset-manifest.json','utf8'));const cssName=manifest.aliases?.['app.css']||manifest['app.css'];
 const fullCss=await readFile('app/static/assets/'+cssName,'utf8');
 server=createServer(async(req,res)=>{
  try{
   if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(`<html lang="ru" data-scheme="dark"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/product.css"><link rel="stylesheet" href="/harness.css"></head><body class="notes-page"><main style="padding:32px 64px;max-width:1100px;margin:auto"><h1>План на неделю</h1><div id="editor"></div></main><script type="module" src="/harness.js"></script></body></html>`);return;}
   if(req.url==='/product.css'){res.setHeader('Content-Type','text/css');res.end(fullCss);return;}
   const path=req.url.split('?')[0].slice(1);if(!/^[\w.-]+$/.test(path)){res.writeHead(404);res.end();return;}
   res.setHeader('Content-Type',path.endsWith('.js')?'text/javascript':path.endsWith('.css')?'text/css':'application/octet-stream');res.end(await readFile(dir+'/'+path));
  }catch{res.writeHead(404);res.end();}
 });await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch();const page=await browser.newPage({viewport:{width:1180,height:800},locale:'ru-RU'}),errors=[];page.on('pageerror',error=>{console.log('Browser notification:',error.message);errors.push(error.message);});
 await page.goto('http://127.0.0.1:'+server.address().port);await page.waitForFunction(()=>window.mount);
 const blocks=[{id:'a',type:'heading',props:{level:2},content:'Дома',children:[]},{id:'b',type:'paragraph',content:'Купить продукты и приготовить ужин',children:[]},{id:'c',type:'paragraph',content:'Позвонить родителям',children:[]}];
 await page.evaluate(blocks=>window.mount(blocks),blocks);await expect(page.locator('.bn-editor')).toBeVisible();await page.evaluate(()=>document.fonts.ready);
 await page.screenshot({path:'artifacts/drg-62/component-before-web-columns.png'});
 await page.evaluate(()=>window.columnActions.wrapColumns(editor,[editor.getBlock('a'),editor.getBlock('b')],3));
 await expect(page.locator('.node-column')).toHaveCount(3);
 let group=await page.evaluate(()=>editor.document.find(b=>b.type==='columnList').id);
 // Use the visible native menu, not an artificial DOM update.
 await page.locator('.node-column').first().locator('..').hover();await page.getByRole('button',{name:'Колонка 1',exact:true}).click();
 await expect(page.getByRole('menuitem',{name:'Добавить колонку справа',exact:true})).toBeVisible();
 await page.waitForTimeout(200);await page.screenshot({path:'artifacts/drg-62/component-after-web-columns-menu.png'});
 await page.getByRole('menuitem',{name:'Добавить колонку справа',exact:true}).click();await expect(page.locator('.node-column')).toHaveCount(4);await expect(page.getByRole('menu')).toHaveCount(0);
 await page.evaluate(()=>editor.undo());await expect(page.locator('.node-column')).toHaveCount(3);
 await page.evaluate(group=>{const g=editor.getBlock(group);editor.updateBlock(g.children[1].children[0],{type:'heading',props:{level:2},content:'Работа'});editor.updateBlock(g.children[2].children[0],{type:'heading',props:{level:2},content:'В выходные'});},group);
 const columns=page.locator('.bn-block-outer').filter({has:page.locator(':scope > .bn-block > .node-column')});
 const positions=await page.locator('.node-column').evaluateAll(nodes=>nodes.map(node=>node.getBoundingClientRect().x));expect(positions[0]).toBeLessThan(positions[1]);expect(positions[1]).toBeLessThan(positions[2]);
 await page.screenshot({path:'artifacts/drg-62/component-after-web-columns.png'});
 const separator=page.getByRole('separator',{name:'Изменить ширину колонок'}).first();await page.locator('.node-column').first().locator('..').hover();const rect=await separator.boundingBox();
 // A Notion layout with a 6.25% numbering column must also be creatable here.
 const pairWidth=await page.locator('.node-column').evaluateAll(nodes=>nodes.slice(0,2).reduce((sum,node)=>sum+node.closest('.bn-block-outer').getBoundingClientRect().width,0));
 await page.mouse.move(rect.x+rect.width/2,rect.y+10);await page.mouse.down();await page.mouse.move(rect.x+rect.width/2-pairWidth*(.5-.0625),rect.y+10,{steps:8});await page.mouse.up();
 const narrow=await page.evaluate(group=>editor.getBlock(group).children.map(c=>c.props.width),group);
 expect(narrow[0]/(narrow[0]+narrow[1])).toBeCloseTo(.0625,3);
 await page.evaluate(()=>editor.undo());
 const widths=await page.evaluate(group=>editor.getBlock(group).children.map(c=>c.props.width),group);const beforeChanges=await page.evaluate(()=>window.changes);
 await page.mouse.move(rect.x+rect.width/2,rect.y+10);await page.mouse.down();await page.mouse.move(rect.x+70,rect.y+10,{steps:15});
 expect(await page.evaluate(group=>editor.getBlock(group).children.map(c=>c.props.width),group)).toEqual(widths);
 await page.mouse.up();await expect.poll(()=>page.evaluate(()=>window.changes)).toBeGreaterThan(beforeChanges);expect(await page.evaluate(()=>window.changes)-beforeChanges).toBeLessThanOrEqual(2);
 const resized=await page.evaluate(group=>editor.getBlock(group).children.map(c=>c.props.width),group);expect(resized[0]).toBeGreaterThan(widths[0]);
 await page.evaluate(()=>editor.undo());expect(await page.evaluate(group=>editor.getBlock(group).children.map(c=>c.props.width),group)).toEqual(widths);
 await page.evaluate(group=>window.columnActions.changeColumns(editor,group,'right',0),group);expect(await page.evaluate(group=>editor.getBlock(group).children[1].children.map(b=>b.id),group)).toEqual(['a','b']);
 await page.evaluate(group=>window.columnActions.changeColumns(editor,group,'merge',1),group);await expect(page.locator('.node-column')).toHaveCount(2);
 await page.evaluate(group=>window.columnActions.changeColumns(editor,group,'unwrap'),group);await expect(page.locator('.node-column')).toHaveCount(0);expect(await page.evaluate(()=>editor.document.flatMap(b=>b.id))).toContain('a');expect(await page.evaluate(()=>editor.getBlock('b').content[0].text)).toContain('Купить продукты');
 await page.evaluate(()=>editor.undo());await expect(page.locator('.node-column')).toHaveCount(2);
 // The side-drop listener uses the same real drag selection as the grip.
 await page.evaluate(()=>window.beginBlockDrag(editor,editor.getBlock('c')));
 const target=await page.locator('.node-column').first().evaluate(node=>node.closest('.bn-block-outer').getBoundingClientRect().toJSON());
 await page.locator('[data-id="a"]').first().dispatchEvent('dragover',{clientX:target.x+2,clientY:target.y+30,dataTransfer:await page.evaluateHandle(()=>new DataTransfer())});
 await expect(page.locator('.drg-column-drop-guide')).toBeVisible();
 await page.locator('[data-id="a"]').first().dispatchEvent('drop',{clientX:target.x+2,clientY:target.y+30,dataTransfer:await page.evaluateHandle(()=>new DataTransfer())});await expect(page.locator('.node-column')).toHaveCount(3);await expect(page.locator('.drg-column-drop-guide')).toHaveCount(0);
 await page.setViewportSize({width:390,height:844});await page.addStyleTag({content:'main{padding:24px 32px!important}'});
 const mobile=await page.locator('.node-column').evaluateAll(nodes=>nodes.map(node=>node.getBoundingClientRect().y));expect(mobile[0]).toBeLessThan(mobile[1]);expect(mobile[1]).toBeLessThan(mobile[2]);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 expect(await page.locator('.bn-block-content h2').evaluateAll(nodes=>nodes.every(node=>node.getBoundingClientRect().left>=30))).toBe(true);await page.screenshot({path:'artifacts/drg-62/component-after-webapp-columns.png'});
 const doc=await page.evaluate(()=>editor.document);await page.evaluate(doc=>window.mount(doc,true),doc);await expect(page.locator('.note-column-menu')).toHaveCount(0);expect(await page.evaluate(()=>columnActions.changeColumns(editor,editor.document[0].id,'after'))).toBe(false);
 expect(errors).toEqual([]);
 const samples=await page.evaluate(async()=>{const values=[];for(let i=0;i<30;i++){await new Promise(requestAnimationFrame);values.push([...document.querySelectorAll('.node-column')].map(node=>{const r=node.closest('.bn-block-outer').getBoundingClientRect();return [r.x,r.y,r.width,r.height];}));}return values;});expect(samples.every(sample=>JSON.stringify(sample)===JSON.stringify(samples[0]))).toBe(true);console.log('No browser errors; stable geometry for 30 frames');console.log('Column editing, one-write resize, undo, side drop, mobile stacking and read-only: passed');
}finally{await browser?.close();if(server)await new Promise(resolve=>server.close(resolve));await rm(dir,{recursive:true,force:true});}
