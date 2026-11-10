// Compare whole rich blocks; previews never become the restore payload.
const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().filter(k=>k!=='id').map(k=>[k,canonical(value[k])])):value;
export const fingerprint=block=>JSON.stringify(canonical(block));
export function compareBlocks(before,after){
  const sameIds=new Set(after.map(b=>b.id)),stable=before.some(b=>sameIds.has(b.id));
  const keys=items=>items.map(b=>stable?b.id:fingerprint(b)),a=keys(before),b=keys(after);
  const grid=Array.from({length:a.length+1},()=>new Uint16Array(b.length+1));
  for(let i=a.length-1;i>=0;i--)for(let j=b.length-1;j>=0;j--)grid[i][j]=a[i]===b[j]?1+grid[i+1][j+1]:Math.max(grid[i+1][j],grid[i][j+1]);
  const document=[],marks=new Map(),counts={added:0,removed:0,changed:0};let serial=0;
  const append=(block,kind)=>{const copy=structuredClone(block);const ids=x=>{x.id='history-'+(++serial);(x.children||[]).forEach(ids);};ids(copy);document.push(copy);if(kind)marks.set(copy.id,kind);};
  let i=0,j=0;
  while(i<a.length||j<b.length){
    if(i<a.length&&j<b.length&&a[i]===b[j]){
      if(fingerprint(before[i])===fingerprint(after[j]))append(after[j]);
      else{counts.changed++;append(before[i],'before');append(after[j],'after');}i++;j++;
    }else if(j<b.length&&(i===a.length||grid[i][j+1]>grid[i+1][j])){counts.added++;append(after[j++],'added');}
    else{counts.removed++;append(before[i++],'removed');}
  }
  return {document,marks,counts};
}
