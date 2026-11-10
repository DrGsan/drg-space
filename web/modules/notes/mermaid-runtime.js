import {tText} from "../../core/i18n.js";
import mermaid from 'mermaid';
import DOMPurify from 'dompurify';
let queue=Promise.resolve();
export function diagram(source,dark=false,active=()=>true){
 const task=queue.then(async()=>{
  if(!active()||!source.trim())return '';
  if(source.length>30000)throw Error(tText("notes:diagramIsTooLargeMaximum30000Characters"));
  // Document content cannot override the viewer's security or resource limits.
  if(/%%\s*\{|^\s*---\s*\n/.test(source))throw Error(tText("notes:settingsForMermaidInTheDiagramTextAre"));
  if(/<\s*(?:img|image|iframe|object|embed)\b|\bimg\s*:|@import|url\s*\(\s*['"]?(?!#)/i.test(source))throw Error(tText("notes:externalImagesAndStylesInDiagramsAreNot"));
  mermaid.initialize({startOnLoad:false,securityLevel:'strict',theme:'base',look:'classic',themeVariables:{darkMode:dark,fontFamily:'Inter, sans-serif',primaryColor:dark?'#303c54':'#eeebff',primaryTextColor:dark?'#edf0f5':'#252b36',primaryBorderColor:dark?'#98a6c8':'#9c8bdd',lineColor:dark?'#c5cde0':'#555b68',secondaryColor:dark?'#263d36':'#ebf6ef',tertiaryColor:dark?'#3d354b':'#f6f1fc'},htmlLabels:false,flowchart:{htmlLabels:false},maxTextSize:30000,maxEdges:500,suppressErrorRendering:true,secure:['securityLevel','startOnLoad','maxTextSize','maxEdges','htmlLabels','flowchart','secure']});
  const container=document.createElement('div');container.style.cssText='position:fixed;left:-100000px;top:0;visibility:hidden';document.body.append(container);
  try{
   const {svg}=await mermaid.render('drgDiagram'+crypto.randomUUID().replaceAll('-',''),source,container);
   const safe=DOMPurify.sanitize(svg,{USE_PROFILES:{svg:true,svgFilters:true},FORBID_TAGS:['foreignObject','image','script','a','iframe','object','embed'],FORBID_ATTR:['href','xlink:href']});
   // Only local paint references are allowed in generated styles.
   const doc=new DOMParser().parseFromString(safe,'image/svg+xml');
   for(const node of doc.querySelectorAll('style,[style]')){
    const css=node.tagName==='style'?node.textContent:node.getAttribute('style');
    if(/@import|url\(\s*['"]?\s*(?!#)[^)]/i.test(css||'')){if(node.tagName==='style')node.remove();else node.removeAttribute('style');}
   }
   return new XMLSerializer().serializeToString(doc.documentElement);
  }finally{container.remove();}
 });
 queue=task.catch(()=>{});return task;
}
