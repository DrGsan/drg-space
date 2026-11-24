// Keep code handles level with their controls. Elsewhere align the handle with
// the actual first line, including in our compact headings.
export const blockSideMenuPosition={name:'drgBlockSideMenu',fn({rects,elements}){
 const reference=elements.reference.contextElement||elements.reference;
 const codeCopy=reference.querySelector?.(':scope > [data-content-type="codeBlock"] > .note-code-actions > button');
 if(codeCopy){
  const box=codeCopy.getBoundingClientRect(),origin=reference.getBoundingClientRect();
  return {y:rects.reference.y+box.top-origin.top+(box.height-rects.floating.height)/2};
 }
 const inline=reference.querySelector?.('.bn-inline-content');
 if(inline){
  const box=inline.getBoundingClientRect(),origin=reference.getBoundingClientRect(),style=getComputedStyle(inline);
  const line=parseFloat(style.lineHeight)||parseFloat(style.fontSize)*1.4;
  return {y:rects.reference.y+box.top-origin.top+(line-rects.floating.height)/2};
 }
 return {y:rects.reference.y+Math.max(0,(Math.min(rects.reference.height,60)-rects.floating.height)/2)};
}};
