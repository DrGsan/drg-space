// BlockNote's stock offsets assume its stock heading sizes. Measure the actual
// first line instead, so our compact headings keep their handles on that line.
export const blockSideMenuPosition={name:'drgBlockFirstLine',fn({rects,elements}){
 const reference=elements.reference.contextElement||elements.reference;
 const inline=reference.querySelector?.('.bn-inline-content');
 if(inline){
  const box=inline.getBoundingClientRect(),origin=reference.getBoundingClientRect(),style=getComputedStyle(inline);
  const line=parseFloat(style.lineHeight)||parseFloat(style.fontSize)*1.4;
  return {y:rects.reference.y+box.top-origin.top+(line-rects.floating.height)/2};
 }
 return {y:rects.reference.y+Math.max(0,(Math.min(rects.reference.height,60)-rects.floating.height)/2)};
}};
