import {tText} from "../../core/i18n.js";
import {planFolder} from './folder-upload.js';

// Capture entries synchronously: browsers clear DataTransfer after the drop event.
export function captureDrop(transfer){
  const items=[...(transfer?.items||[])].filter(item=>item.kind==='file');
  const entries=items.map(item=>item.webkitGetAsEntry?.()||null);
  if(entries.some(Boolean)){
    if(entries.some(entry=>!entry))throw Error(tText("files:theBrowserWasUnableToReadOneOf"));
    return entries.map((entry,i)=>({entry,file:entry.isFile?items[i].getAsFile?.():null}));
  }
  return [...(transfer?.files||[])].map(file=>({file}));
}
export async function readDrop(captured){
  const result=[];let total=0,folderCount=0;
  const fileOf=entry=>new Promise((resolve,reject)=>entry.file(resolve,reject));
  for(const item of captured){
    if(item.file){if(++total>10000)throw Error(tText("files:noMoreThan10000FilesPerUpload"));result.push({file:item.file});continue;}
    const root=item.entry;
    if(root.isFile){if(++total>10000)throw Error(tText("files:noMoreThan10000FilesPerUpload"));result.push({file:await fileOf(root)});continue;}
    const files=[],folders=[];
    async function walk(dir,path,depth){
      if(depth>32)throw Error(tText("files:noMoreThan32LevelsOfNesting"));
      if(++folderCount>2048)throw Error(tText("files:noMoreThan2048FoldersPerUpload"));
      folders.push(path);
      const reader=dir.createReader();
      // readEntries returns batches (often 100 entries), not the entire directory.
      for(;;){
        const entries=await new Promise((resolve,reject)=>reader.readEntries(resolve,reject));
        if(!entries.length)break;
        for(const entry of entries){
          const next=path?path+'/'+entry.name:entry.name;
          if(entry.isDirectory)await walk(entry,next,depth+1);
          else{if(++total>10000)throw Error(tText("files:noMoreThan10000FilesPerUpload"));files.push({path:next,file:await fileOf(entry)});}
        }
      }
    }
    await walk(root,'',0);result.push({folder:planFolder(root.name,files,folders)});
  }
  if(!result.length)throw Error(tText("files:dragFilesOrFoldersFromYourDevice"));
  return result;
}
