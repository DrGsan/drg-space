import React,{useEffect,useState} from 'react';
import {createReactInlineContentSpec} from '@blocknote/react';

const h=React.createElement;
const widthValue=value=>Number.isInteger(value)&&value>0&&value<=9999?value:0;

function imageUrl(value){
  try{
    const url=new URL(value,location.origin);
    if(['https:','http:','blob:'].includes(url.protocol))return url.href;
  }catch{}
  return '';
}

function InlineImage({inlineContent,editor}){
  const {url,alt,width}=inlineContent.props;
  const [resolved,setResolved]=useState('');
  useEffect(()=>{
    let attached=true;
    Promise.resolve()
      .then(()=>editor.resolveFileUrl?editor.resolveFileUrl(url):url)
      .then(value=>{if(attached)setResolved(imageUrl(value));},()=>{if(attached)setResolved('');});
    return ()=>{attached=false;};
  },[editor,url]);
  return resolved?h('img',{className:'note-inline-image',src:resolved,alt,width:widthValue(width)||undefined,loading:'lazy'}):h('span',null,alt);
}

export const inlineImage=createReactInlineContentSpec({
  type:'inlineImage',
  propSchema:{url:{default:''},alt:{default:''},width:{default:0}},
  content:'none',
},{
  render:props=>h(InlineImage,props),
  parse:element=>{
    const url=element.getAttribute('data-note-inline-image');
    if(url&&imageUrl(url))return {url,alt:element.getAttribute('data-alt')||'',width:widthValue(Number(element.getAttribute('data-width')))};
  },
  toExternalHTML:({inlineContent})=>{
    const {url,alt,width}=inlineContent.props;
    return h('span',{'data-note-inline-image':url,'data-alt':alt,'data-width':width},
      h('img',{src:imageUrl(url)||undefined,alt,width:widthValue(width)||undefined}));
  },
});
