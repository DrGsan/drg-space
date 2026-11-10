import {tText} from "./i18n.js";
// Decode locally and send a small, oriented raster instead of the camera
// original. The server still validates and creates its private 256px avatar.
export const avatarHint=tText("common:pngJpegWebpOrGifLargePhotosAre");
export async function prepareAvatar(file){
  if(file.size>50*1024*1024)throw Error(tText("common:theOriginalPhotoMustBeNoMoreThan"));
  const url=URL.createObjectURL(file),image=new Image();
  try{
    await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(Error(tText("common:couldNotOpenPhotoSelectPngJpegWebp")));image.src=url;});
    if(!image.naturalWidth||!image.naturalHeight)throw Error(tText("common:thePhotoHasNoImage"));
    const scale=Math.min(1,1024/Math.max(image.naturalWidth,image.naturalHeight)),canvas=document.createElement('canvas');
    canvas.width=Math.max(1,Math.round(image.naturalWidth*scale));canvas.height=Math.max(1,Math.round(image.naturalHeight*scale));
    const context=canvas.getContext('2d');if(!context)throw Error(tText("common:couldNotPrepareThePhoto"));
    context.drawImage(image,0,0,canvas.width,canvas.height);
    const encode=(type,quality)=>new Promise((resolve,reject)=>canvas.toBlob(blob=>blob?resolve(blob):reject(Error(tText("common:failedToReducePhoto"))),type,quality));
    let blob=await encode('image/webp',.86);
    // Safari can return PNG when canvas has no WebP encoder. Compress opaque
    // photos as JPEG in that case, retaining PNG for actual transparency.
    if(blob.type!=='image/webp'){
      const pixels=context.getImageData(0,0,canvas.width,canvas.height).data;
      let alpha=false;for(let i=3;i<pixels.length;i+=4)if(pixels[i]<255){alpha=true;break;}
      if(!alpha)blob=await encode('image/jpeg',.88);
    }
    return blob;
  }finally{image.src='';URL.revokeObjectURL(url);}
}
export function avatarDataUrl(blob){
  return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(Error(tText("common:failedToReadPhoto")));reader.readAsDataURL(blob);});
}
