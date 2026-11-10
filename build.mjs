import {build} from 'esbuild';
import {createHash} from 'node:crypto';
import {mkdir,cp,rm,readFile,writeFile} from 'node:fs/promises';

await rm('app/static/assets',{recursive:true,force:true});
await mkdir('app/static/assets',{recursive:true});
// One module graph keeps the locale runtime and shared dependencies singleton.
// Dynamic imports become independent files and load only when their view needs them.
const entryPoints={app:'web/app.js','locale-boot':'web/locale-boot.js',i18n:'web/core/i18n.js','native-passkey-create':'web/native-passkey-create.js','native-passkey':'web/native-passkey.js',mermaid:'web/modules/notes/mermaid-runtime.js'};
const result=await build({
  entryPoints,
  outdir:'app/static/assets',entryNames:'[name]-[hash]',chunkNames:'chunk-[name]-[hash]',assetNames:'[name]-[hash]',
  bundle:true,minify:true,splitting:true,format:'esm',target:['es2022'],conditions:['style'],metafile:true,
  define:{'process.env.NODE_ENV':'"production"'},
  loader:{'.woff2':'file','.woff':'file','.ttf':'file','.svg':'file','.png':'file'},
});
const aliases={},immutable=[];
for(const [path,value] of Object.entries(result.metafile.outputs)){
  const name=path.replace('app/static/assets/','');immutable.push(name);
  if(value.entryPoint){
    const key=Object.entries(entryPoints).find(([,source])=>source===value.entryPoint)?.[0];
    if(key&&name.endsWith('.js'))aliases[key+'.js']=name;
  }
}
// Keep the established stylesheet order and all editor styles available before
// mounting a lazy editor. Output files share one directory, including font URLs.
const appOutput=Object.values(result.metafile.outputs).find(value=>value.entryPoint==='web/app.js'&&value.cssBundle)?.cssBundle;
const css=(await readFile(appOutput,'utf8'))+'\n'+await readFile('web/style.css','utf8')+'\n'+await readFile('web/themes.css','utf8')+'\n'+await readFile('web/glass.css','utf8');
const cssName='app-'+createHash('sha256').update(css).digest('hex').slice(0,16)+'.css';
// The HTML links one complete stylesheet; companion CSS files are not imported by JS.
for(const path of Object.keys(result.metafile.outputs).filter(path=>path.endsWith('.css'))){await rm(path);immutable.splice(immutable.indexOf(path.replace('app/static/assets/','')),1);}
await writeFile('app/static/assets/'+cssName,css);aliases['app.css']=cssName;immutable.push(cssName);
for(const page of ['index','native-passkey-create','native-passkey']){
  let html=await readFile('web/'+page+'.html','utf8');
  for(const [alias,target] of Object.entries(aliases))html=html.replaceAll('/assets/'+alias,'/assets/'+target);
  await writeFile('app/static/'+page+'.html',html);
}
await cp('web/public/assets','app/static/assets',{recursive:true});
await mkdir('app/static/assets/manifests',{recursive:true});
const manifestBase=JSON.parse(await readFile('web/public/assets/manifest.webmanifest','utf8'));
for(const language of Object.keys(JSON.parse(await readFile('locales/languages.json','utf8')).languages)){
  const messages=JSON.parse(await readFile(`locales/${language}/auth.json`,'utf8'));
  const manifest={...manifestBase,lang:language,description:messages.description};
  await writeFile(`app/static/assets/manifests/${language}.webmanifest`,JSON.stringify(manifest));
  if(language==='ru')await writeFile('app/static/assets/manifest.webmanifest',JSON.stringify(manifest));
}
const pushMessages={};
for(const language of Object.keys(JSON.parse(await readFile('locales/languages.json','utf8')).languages))pushMessages[language]=JSON.parse(await readFile(`locales/${language}/push.json`,'utf8'));
await build({entryPoints:['web/public/push-sw.js'],bundle:true,minify:true,format:'iife',outfile:'app/static/push-sw.js',target:['es2022'],define:{DRG_PUSH_MESSAGES:JSON.stringify(pushMessages)}});
await writeFile('app/static/asset-manifest.json',JSON.stringify({aliases,immutable:[...new Set(immutable)].sort()}));
if(process.env.DRG_BUILD_METAFILE)await writeFile(process.env.DRG_BUILD_METAFILE,JSON.stringify(result.metafile));
console.log('Built content-addressed assets; editor, emoji and diagrams load on demand.');
