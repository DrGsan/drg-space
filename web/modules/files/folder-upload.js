import {tText} from "../../core/i18n.js";
import {uploadFile} from './upload-panel.js';
import {withUploadRateRetry} from './upload-retry.js';
import {bytes} from '../../core/ui.js';
const MAX_FILES=10000,MAX_FOLDERS=2048,MAX_DEPTH=32;
function segments(path){
 const parts=path.split('/').map(x=>x.trim());
 if(parts.some(x=>!x||x==='.'||x==='..'||x.length>255||/[\\:\x00-\x1f]/.test(x)))throw Error(tText("files:invalidNameInFolder", {value0:(path)}));
 if(parts.length>MAX_DEPTH+1)throw Error(tText("files:noMoreThan32LevelsOfNesting"));
 return parts;
}
export function planFolder(name,entries,emptyFolders=[]){
 if(segments(name).length!==1)throw Error(tText("files:invalidFolderName"));
 const folders=new Set(['']),paths=new Set();
 const add=path=>{if(!path)return;const parts=segments(path);if(parts.length>MAX_DEPTH)throw Error(tText("files:noMoreThan32LevelsOfNesting"));for(let n=1;n<=parts.length;n++)folders.add(parts.slice(0,n).join('/'));};
 if(entries.length>MAX_FILES)throw Error(tText("files:noMoreThan10000FilesPerUpload"));
 for(const path of emptyFolders)add(path);
 const files=entries.map(({path,file})=>{const parts=segments(path),normalized=parts.join('/');if(paths.has(normalized))throw Error(tText("files:duplicatePath", {value0:(normalized)}));paths.add(normalized);const parent=parts.slice(0,-1).join('/');add(parent);return {path:normalized,parent,name:parts.at(-1),file};});
 if(folders.size>MAX_FOLDERS)throw Error(tText("files:noMoreThan2048FoldersPerUpload"));
 if(files.some(f=>folders.has(f.path)))throw Error(tText("files:fileAndFolderHaveTheSamePath"));
 return {name:name.trim(),folders:[...folders],files};
}
export function fromFileList(list){
 const files=[...list];if(!files.length)throw Error(tText("files:noFilesInTheSelectedFolderThisSelection"));
 let root;
 const entries=files.map(file=>{const parts=file.webkitRelativePath.split('/');if(parts.length<2)throw Error(tText("files:selectAFolderNotIndividualFiles"));root??=parts[0];if(root!==parts[0])throw Error(tText("files:selectOneRootFolder"));return {path:parts.slice(1).join('/'),file};});
 return planFolder(root,entries);
}
export async function fromDirectory(handle){
 const entries=[],folders=[];
 async function walk(dir,path,depth){
  if(depth>MAX_DEPTH)throw Error(tText("files:noMoreThan32LevelsOfNesting"));
  folders.push(path);if(folders.length>MAX_FOLDERS)throw Error(tText("files:noMoreThan2048FoldersPerUpload"));
  for await(const [name,item] of dir.entries()){
   const next=path?path+'/'+name:name;
   if(item.kind==='directory')await walk(item,next,depth+1);
   else{if(entries.length>=MAX_FILES)throw Error(tText("files:noMoreThan10000FilesPerUpload"));entries.push({path:next,file:await item.getFile()});}
  }
 }
 await walk(handle,'',0);return planFolder(handle.name,entries,folders);
}
export async function uploadFolder(plan,ctx,parent,data,transfer){
 for(const item of plan.files)if(item.file.size>data.max_file_bytes)throw Error(tText("files:fileIsTooLargeMaximum", {value0:(item.path), value1:(bytes(data.max_file_bytes))}));
 const result=await withUploadRateRetry(()=>ctx.api('/files/folder-import','POST',{name:plan.name,parent_id:parent,folders:plan.folders}),transfer);
 let grant=result,deadline=performance.now()+Math.max(0,result.upload_ttl-30)*1000;
 const renew=async()=>{grant=await withUploadRateRetry(()=>ctx.api('/files/folder-import/'+result.root.id+'/renew','POST'),transfer);deadline=performance.now()+Math.max(0,grant.upload_ttl-30)*1000;};
 if(!plan.files.length){transfer.start();transfer.complete();}
 for(const item of plan.files){
  if(transfer.isStopped())break;
  transfer.start();
  if(performance.now()>=deadline)await renew();
  const send=()=>withUploadRateRetry(()=>uploadFile('/files/upload?name='+encodeURIComponent(item.name)+'&parent_id='+result.folders[item.parent],item.file,{'X-Folder-Upload-Token':grant.upload_token},transfer.progress),transfer);
  try{await send();}catch(error){if(error.status!==401)throw error;await renew();await send();}
  transfer.complete();
 }
 return result.root;
}
