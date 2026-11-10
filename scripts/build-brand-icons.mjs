// Repackage the approved D8 artwork; production builds use the checked-in PNGs.
import {chromium} from 'playwright';
import {readFile,writeFile,mkdir} from 'node:fs/promises';

const source=new URL('../design/branding/drg-d8.png',import.meta.url);
const destination=new URL('../web/public/assets/icons/',import.meta.url);
const data='data:image/png;base64,'+(await readFile(source)).toString('base64');
const browser=await chromium.launch({headless:true});
try{
  const page=await browser.newPage();
  const images=await page.evaluate(async data=>{
    const image=new Image();image.src=data;await image.decode();
    const original=document.createElement('canvas');original.width=image.width;original.height=image.height;
    const context=original.getContext('2d');context.drawImage(image,0,0);
    const pixels=context.getImageData(0,0,image.width,image.height).data;
    // Crop transparent padding, ignoring isolated antialiasing specks around
    // the source. The artwork itself is only uniformly scaled, never redrawn.
    const rows=new Uint32Array(image.height),columns=new Uint32Array(image.width);
    for(let y=0;y<image.height;y++)for(let x=0;x<image.width;x++)if(pixels[(y*image.width+x)*4+3]>160){rows[y]++;columns[x]++;}
    const left=columns.findIndex(n=>n>8),top=rows.findIndex(n=>n>8);
    const right=columns.findLastIndex(n=>n>8)+1,bottom=rows.findLastIndex(n=>n>8)+1;
    if(left<0||top<0)throw Error('D8 artwork is empty.');
    const entries=[
      ['drg-d8-logo.png',256,1,null],
      ['drg-d8-32.png',32,.96,null],
      ['drg-d8-192.png',192,.82,'#111a21'],
      ['drg-d8-512.png',512,.82,'#111a21'],
      ['drg-d8-maskable-512.png',512,.56,'#111a21'],
      ['drg-d8-apple-180.png',180,.82,'#111a21'],
    ];
    return entries.map(([name,size,scale,background])=>{
      const canvas=document.createElement('canvas');canvas.width=size;canvas.height=size;
      const ctx=canvas.getContext('2d');ctx.imageSmoothingEnabled=true;ctx.imageSmoothingQuality='high';
      if(background){ctx.fillStyle=background;ctx.fillRect(0,0,size,size);}
      const factor=size*scale/Math.max(right-left,bottom-top),width=(right-left)*factor,height=(bottom-top)*factor;
      ctx.drawImage(image,left,top,right-left,bottom-top,(size-width)/2,(size-height)/2,width,height);
      return {name,data:canvas.toDataURL('image/png').split(',')[1]};
    });
  },data);
  await mkdir(destination,{recursive:true});
  for(const image of images){const bytes=Buffer.from(image.data,'base64');await writeFile(new URL(image.name,destination),bytes);console.log(image.name,bytes.length);}
}finally{await browser.close();}
