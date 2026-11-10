import {tText} from "../../core/i18n.js";
// Keep phone originals local. Upload only a bounded, oriented still image on
// Save; no camera metadata or animation is needed for a workspace background.
export async function prepareWallpaper(file){
  if(file.size>50*1024*1024)throw Error(tText("appearance:theImageShouldNotExceed50Mb"));
  if(!['image/jpeg','image/png','image/webp','image/gif'].includes(file.type))throw Error(tText("appearance:choosePngJpegWebpOrGif"));
  const url=URL.createObjectURL(file),image=new Image();
  try{
    await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(Error(tText("appearance:couldNotOpenImageChooseAnother")));image.src=url;});
    const scale=Math.min(1,2560/Math.max(image.naturalWidth,image.naturalHeight));
    const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(image.naturalWidth*scale));canvas.height=Math.max(1,Math.round(image.naturalHeight*scale));
    const context=canvas.getContext('2d');if(!context)throw Error(tText("appearance:couldNotPrepareTheImage"));
    context.fillStyle='#f3eee5';context.fillRect(0,0,canvas.width,canvas.height);context.drawImage(image,0,0,canvas.width,canvas.height);
    const encode=type=>new Promise((resolve,reject)=>canvas.toBlob(blob=>blob?resolve(blob):reject(Error(tText("appearance:couldNotReduceTheImage"))),type,.86));
    let blob=await encode('image/webp');if(blob.type!=='image/webp')blob=await encode('image/jpeg');
    if(blob.size>10*1024*1024)throw Error(tText("appearance:theImageIsTooComplexChooseAnotherPhoto"));
    return blob;
  }finally{image.src='';URL.revokeObjectURL(url);}
}
