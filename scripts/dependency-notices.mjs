import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

// Run after npm ci. Missing upstream notices must be supplied explicitly for
// that exact package version; dependency changes never borrow another version.
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const directory=path.join(root,'web/public/assets/licenses');
const catalogue=path.join(directory,'packages.json');
const prior=JSON.parse(fs.readFileSync(catalogue,'utf8'));
const lock=JSON.parse(fs.readFileSync(path.join(root,'package-lock.json'),'utf8'));
const previous=new Map(prior.map(item=>[`${item.name}@${item.version}`,item]));
const files=new Map(),records=[],seen=new Set();
for(const [location,metadata] of Object.entries(lock.packages)){
  if(!location||metadata.dev||!location.includes('node_modules/'))continue;
  const installed=path.join(root,location);
  const pkg=JSON.parse(fs.readFileSync(path.join(installed,'package.json'),'utf8'));
  if(pkg.version!==metadata.version)throw new Error(`Run npm ci: ${location} differs from package-lock.json`);
  const id=`${pkg.name}@${pkg.version}`;
  if(seen.has(id))continue;
  seen.add(id);
  const prefix=id.replace(/[^A-Za-z0-9._-]/g,'_')+'--';
  const notices=[];
  for(const name of fs.readdirSync(installed).sort()){
    const source=path.join(installed,name);
    if(!/^(?:licen[cs]e|copying|notice)(?:[._-].*)?$/i.test(name)||!fs.statSync(source).isFile())continue;
    const target=prefix+name;files.set(target,fs.readFileSync(source));notices.push(target);
  }
  const old=previous.get(id),sources=old?.notice_sources;
  // Some published packages omit their root licence. Retain the separately
  // verified upstream notice only while the exact package version is pinned.
  if(!notices.length&&old?.notice_files.length){
    for(const name of old.notice_files){
      const target=prefix+name.split('--').slice(1).join('--');
      files.set(target,fs.readFileSync(path.join(directory,name)));notices.push(target);
    }
  }
  if(!notices.length)throw new Error(`Missing upstream licence text: ${id}`);
  const item={name:pkg.name,version:pkg.version,license:pkg.license||metadata.license||'See upstream',repository:pkg.repository,notice_files:notices};
  if(sources)item.notice_sources=sources;
  records.push(item);
}
files.set('packages.json',Buffer.from(JSON.stringify(records,null,2)+'\n'));
const expected=new Set(files.keys());
if(process.argv.includes('--check')){
  const different=[...files].filter(([name,contents])=>!fs.existsSync(path.join(directory,name))||!fs.readFileSync(path.join(directory,name)).equals(contents)).map(([name])=>name);
  const extra=fs.readdirSync(directory).filter(name=>!expected.has(name));
  if(different.length||extra.length)throw new Error('Dependency notices are stale. Run npm run licenses:update after npm ci and review the changes.');
}else{
  for(const [name,contents] of files)fs.writeFileSync(path.join(directory,name),contents);
  for(const name of new Set(prior.flatMap(item=>item.notice_files)))if(!expected.has(name))fs.unlinkSync(path.join(directory,name));
}
console.log(`Dependency notices: ${records.length} pinned production packages, ${files.size-1} licence files`);
