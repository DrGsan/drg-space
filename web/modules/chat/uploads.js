import {tText} from "../../core/i18n.js";
import {abortable} from '../../core/request.js';
// Preview mode bounds still images before upload. Document mode never touches
// the original bytes; GIFs retain their animation in either mode.
export async function previewFile(file,{signal}={}){
  const heic=/image\/hei[cf]/.test(file.type)||/\.hei[cf]$/i.test(file.name);
  if(!heic&&!['image/jpeg','image/png','image/webp'].includes(file.type))return file;
  const url=URL.createObjectURL(file);
  try{
    const image=new Image();image.src=url;
    try{await abortable(()=>image.decode(),{signal,timeoutMs:20000,message:tText("chat:imageProcessingTookTooLongSendItAs")});}catch(error){if(signal?.aborted||error.name==='TimeoutError')throw error;throw new Error(tText("chat:theBrowserCouldNotOpenTheImageSend"));}
    if(image.width*image.height>40000000)throw new Error(tText("chat:theImageIsTooLargeToProcessSend"));
    const scale=Math.min(1,1920/Math.max(image.width,image.height));
    const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(image.width*scale));canvas.height=Math.max(1,Math.round(image.height*scale));
    canvas.getContext('2d').drawImage(image,0,0,canvas.width,canvas.height);
    const blob=await abortable(()=>new Promise(resolve=>canvas.toBlob(resolve,'image/webp',.86)),{signal,timeoutMs:20000,message:tText("chat:previewCouldNotBePreparedSendTheImage")});
    return blob&&(blob.size<file.size||heic)?new File([blob],file.name.replace(/\.[^.]+$/,'')+(blob.type==='image/webp'?'.webp':'.png'),{type:blob.type}):file;
  }finally{URL.revokeObjectURL(url);}
}
