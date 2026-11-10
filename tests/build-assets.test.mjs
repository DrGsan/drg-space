import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,cp,copyFile,symlink,readFile,writeFile,rm,mkdir,readdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {execFileSync} from 'node:child_process';

test('build hashes invalidate changed code and keep heavy editor dependencies out of startup',async()=>{
 const directory=await mkdtemp(tmpdir()+'/drg-asset-build-');
 try{
  await cp('web',directory+'/web',{recursive:true});await cp('locales',directory+'/locales',{recursive:true});await copyFile('build.mjs',directory+'/build.mjs');await copyFile('package.json',directory+'/package.json');await symlink(resolve('node_modules'),directory+'/node_modules');
  await mkdir(directory+'/app');for(const name of await readdir('app'))if(name.endsWith('.json'))await copyFile('app/'+name,directory+'/app/'+name);
  const build=()=>execFileSync(process.execPath,['build.mjs'],{cwd:directory,env:{...process.env,DRG_BUILD_METAFILE:directory+'/metafile.json'},stdio:'pipe'});
  build();const first=JSON.parse(await readFile(directory+'/app/static/asset-manifest.json','utf8')),metadata=JSON.parse(await readFile(directory+'/metafile.json','utf8'));
  const seen=new Set(),outputs=metadata.outputs;
  const visit=path=>{if(seen.has(path))return;seen.add(path);for(const edge of outputs[path].imports||[])if(edge.kind!=='dynamic-import'&&outputs[edge.path])visit(edge.path);};
  visit('app/static/assets/'+first.aliases['app.js']);visit('app/static/assets/'+first.aliases['locale-boot.js']);
  assert.ok([...seen].reduce((n,path)=>n+outputs[path].bytes,0)<3_000_000);
  for(const path of seen)for(const input of Object.keys(outputs[path].inputs))assert.ok(!/node_modules\/(?:@blocknote|react-dom|emojibase-data|@shikijs)\//.test(input),'Loaded too early: '+input);
  const html=await readFile(directory+'/app/static/index.html','utf8');assert.ok(html.includes(first.aliases['locale-boot.js']));assert.ok(html.includes(first.aliases['app.css']));
  const css=await readFile(directory+'/app/static/assets/'+first.aliases['app.css'],'utf8');assert.ok(css.includes('.bn-editor'),'Lazy editor styles must be ready before mounting');
  await writeFile(directory+'/web/app.js',(await readFile(directory+'/web/app.js','utf8'))+'\nconsole.info("asset upgrade fixture");\n');
  build();const second=JSON.parse(await readFile(directory+'/app/static/asset-manifest.json','utf8'));
  assert.notEqual(first.aliases['app.js'],second.aliases['app.js']);assert.notEqual(first.aliases['locale-boot.js'],second.aliases['locale-boot.js']);assert.equal(first.aliases['app.css'],second.aliases['app.css']);
  assert.ok((await readFile(directory+'/app/static/index.html','utf8')).includes(second.aliases['locale-boot.js']));
 }finally{await rm(directory,{recursive:true,force:true});}
});
