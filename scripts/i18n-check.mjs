import {readFile, readdir, writeFile} from 'node:fs/promises';
import {parse, parseExpression} from '@babel/parser';
import path from 'node:path';
const configuration=JSON.parse(await readFile('locales/languages.json','utf8'));
const languages=Object.keys(configuration.languages),base=configuration.defaultLanguage;
const catalogs={},errors=[];
const namespaces=(await readdir(`locales/${base}`)).filter(file=>file.endsWith('.json')).map(file=>file.slice(0,-5)).sort();
const variables=value=>[...value.matchAll(/\{\{(\w+)\}\}/g)].map(match=>match[1]).sort().join(',');
for(const language of languages){
 catalogs[language]={};
 const actual=(await readdir(`locales/${language}`)).filter(file=>file.endsWith('.json')).map(file=>file.slice(0,-5)).sort();
 if(JSON.stringify(namespaces)!==JSON.stringify(actual))errors.push(`${language}: namespaces differ`);
 for(const ns of namespaces){
  const file=`locales/${language}/${ns}.json`;let raw;
  try{raw=await readFile(file,'utf8');}catch{errors.push(`${file}: missing file`);continue;}
  const ast=parseExpression('('+raw+')'),seen=new Set();
  for(const property of ast.properties){const key=property.key.value;if(seen.has(key))errors.push(`${file}: duplicate ${key}`);seen.add(key);}
  const table=JSON.parse(raw);catalogs[language][ns]=table;
  for(const [key,value]of Object.entries(table)){
   if(typeof value!=='string'||!value.trim()){errors.push(`${file}:${key}: empty or non-string value`);continue;}
   if(/<\/?[a-z][^>]*>/i.test(value))errors.push(`${file}:${key}: markup in translation`);
   if(language==='en'&&/[А-Яа-яЁё]/.test(value))errors.push(`${file}:${key}: untranslated Cyrillic`);
   if(/DRGEXPR|DRGTRAN|ZXQ|QXZ/.test(value))errors.push(`${file}:${key}: extraction marker`);
  }
 }
}
const pluralParts=key=>/^(.*)_(zero|one|two|few|many|other)$/.exec(key);
const logicalKeys=table=>[...new Set(Object.keys(table).map(key=>pluralParts(key)?.[1]||key))].sort();
for(const language of languages)for(const ns of namespaces){
 const source=catalogs[base][ns]||{},target=catalogs[language][ns]||{};
 if(JSON.stringify(logicalKeys(source))!==JSON.stringify(logicalKeys(target)))errors.push(`${language}/${ns}: logical keys differ`);
 const groups=new Set(Object.keys(target).map(key=>pluralParts(key)?.[1]).filter(Boolean));
 for(const group of groups)for(const form of new Intl.PluralRules(language).resolvedOptions().pluralCategories)if(target[group+'_'+form]===undefined)errors.push(`${language}/${ns}:${group}: missing plural form ${form}`);
 for(const [key,value]of Object.entries(target)){
  const plural=pluralParts(key),reference=source[key]??(plural?source[plural[1]+'_other']:undefined);
  if(typeof reference==='string'&&variables(reference)!==variables(value))errors.push(`${language}/${ns}:${key}: interpolation variables differ`);
 }
}
const exists=key=>{const [ns,name]=key.split(':');return catalogs[base][ns]?.[name]!==undefined||catalogs[base][ns]?.[name+'_other']!==undefined;};
function walk(node,visit,parent){if(!node||typeof node!=='object')return;if(node.type)visit(node,parent);for(const [key,value]of Object.entries(node)){if(['loc','extra','comments'].includes(key))continue;if(Array.isArray(value))for(const child of value)walk(child,visit,node);else if(value&&typeof value==='object')walk(value,visit,node);}}
async function scan(directory){for(const entry of await readdir(directory,{withFileTypes:true})){
 const file=path.join(directory,entry.name);if(entry.isDirectory()){await scan(file);continue;}if(!file.endsWith('.js'))continue;
 const source=await readFile(file,'utf8'),ast=parse(source,{sourceType:'module'});
 walk(ast,(node,parent)=>{
  if(node.type==='CallExpression'&&['tText','tHtml'].includes(node.callee.name)&&node.arguments[0]?.type==='StringLiteral'&&!exists(node.arguments[0].value))errors.push(`${file}:${node.loc.start.line}: missing key ${node.arguments[0].value}`);
  if((node.type==='StringLiteral'||node.type==='TemplateElement')&&/[А-Яа-яЁё]/.test(node.value?.cooked??node.value??'')){
   if(parent?.key===node&&!parent.computed)return;
   // Russian е/ё folding is search logic, independent of the interface language.
   if(file==='web/modules/admin/friends.js'&&parent?.type==='CallExpression'&&parent.callee.property?.name==='replaceAll'&&['е','ё'].includes(node.value))return;
   errors.push(`${file}:${node.loc.start.line}: hardcoded interface text`);
  }
 });
}}
await scan('web');
async function checkServer(directory){for(const entry of await readdir(directory,{withFileTypes:true})){
 const file=path.join(directory,entry.name);if(entry.isDirectory()){if(entry.name!=='static'&&entry.name!=='__pycache__')await checkServer(file);continue;}if(!file.endsWith('.py'))continue;
 const source=await readFile(file,'utf8');
 for(const match of source.matchAll(/(?<![\w.])(?:localized_message|message|localized_text|text)\(['"]([a-zA-Z0-9_]+)['"]/g))if(catalogs[base].server[match[1]]===undefined)errors.push(`${file}: missing server key ${match[1]}`);
}}
await checkServer('app');
const imports=[],entries=[];
for(const language of languages){const names=[];for(const ns of namespaces){const variable=language+'_'+ns;imports.push(`import ${variable} from './${language}/${ns}.json' with {type:'json'};`);names.push(ns+':'+variable);}entries.push(language+':{'+names.join(',')+'}');}
const resourceSource='// Generated by npm run i18n:resources. Edit JSON catalogs instead.\n'+imports.join('\n')+'\nexport default {'+entries.join(',')+'};\n';
if(process.argv.includes('--write-resources'))await writeFile('locales/resources.js',resourceSource);
else if(await readFile('locales/resources.js','utf8')!==resourceSource)errors.push('Run npm run i18n:resources to update the resource registry');
if(errors.length){console.error(errors.join('\n'));process.exitCode=1;}else console.log(`Localization: ${languages.length} languages, ${namespaces.length} namespaces, ${Object.values(catalogs[base]).reduce((count,table)=>count+Object.keys(table).length,0)} keys; catalogs and source coverage OK.`);
