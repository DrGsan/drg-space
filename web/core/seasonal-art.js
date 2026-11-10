// The selected “holiday frame” artwork. Each SVG owns its gradient identifiers,
// including when the same theme appears in several independent workspace bars.

import {holidayObject,seasonalObjectIds} from './seasonal-objects.js';
export const seasonalArtworkIds=seasonalObjectIds;

let serial=0;
export function holidaySymbol(theme,index=0){
  const id='seasonal-art-'+(++serial),gradient=(a,b)=>`<defs><radialGradient id="${id}" cx="28%" cy="22%" r="90%"><stop stop-color="${a}"/><stop offset=".55" stop-color="${b}"/><stop offset="1" stop-color="#343047"/></radialGradient></defs>`;
  let body='';
  if(theme==='newyear'){
    if(index%3===2)body='<path d="m32 4 7 19 21 1-16 14 5 21-17-12-18 12 6-21L3 24l22-1Z" fill="#e1bb60"/><path d="m32 4 7 19-7 9-7-9Z" fill="#fff0bb"/>';
    else body=gradient(index%3?'#b3e5cc':'#fff0b9',index%3?'#3e8d79':'#cfa447')+'<path d="M32 1v7" stroke="#f5d789" stroke-width="2"/><rect x="25" y="6" width="14" height="7" rx="2" fill="#e5bb5b"/><circle cx="32" cy="36" r="25" fill="url(#'+id+')"/><path d="M9 37q20 9 46-12" fill="none" stroke="#fff0b9" stroke-width="4"/><ellipse cx="23" cy="22" rx="8" ry="4" transform="rotate(-30 23 22)" fill="#fff7"/>';
  }else if(theme==='halloween'){
    if(index%3===1)body='<path d="M12 58V27C12 0 52 0 52 27v31l-8-7-8 8-8-8-8 8Z" fill="#efe7ec"/><ellipse cx="25" cy="28" rx="4" ry="6" fill="#51435f"/><ellipse cx="40" cy="28" rx="4" ry="6" fill="#51435f"/>';
    else body=gradient('#ffd18a','#ed8637')+'<path d="m29 15 3-12 8 2-5 12" fill="#688051"/><path d="M32 14C6 4-4 28 8 48c9 18 39 18 49-1C68 27 56 3 32 14Z" fill="url(#'+id+')"/><path d="m15 29 8-7 5 10Zm21 3 5-10 8 7ZM17 42l8 3 7-2 8 2 8-3q-16 23-31 0" fill="#ffe4a0"/>';
  }else if(theme==='valentine'){
    body=gradient('#ffd7df',index%2?'#d87d99':'#df6889')+'<path d="M32 58 9 35C-12 12 17-7 32 13 49-7 77 12 55 35Z" fill="url(#'+id+')"/><path d="M14 13q-8 5-3 13" fill="none" stroke="#fff7" stroke-width="4" stroke-linecap="round"/>';
  }else if(theme==='spring'){
    body=gradient(index%2?'#fff3be':'#ffd4e3',index%2?'#eabf7d':'#df91b4')+Array.from({length:6},(_,i)=>`<ellipse cx="32" cy="17" rx="11" ry="15" transform="rotate(${i*60} 32 32)" fill="url(#${id})"/>`).join('')+'<circle cx="32" cy="32" r="9" fill="#f7d375"/><circle cx="29" cy="29" r="3" fill="#fff0bd"/>';
  }else if(theme==='space'){
    if(index%3===2)body=gradient('#f6f0e5','#c2c9d9')+'<circle cx="32" cy="32" r="25" fill="url(#'+id+')"/><g fill="#919caf66"><circle cx="22" cy="22" r="6"/><circle cx="40" cy="40" r="8"/><circle cx="42" cy="19" r="4"/></g>';
    else body=gradient(index%2?'#bce3f2':'#ffdfaa',index%2?'#7777b9':'#d69a7f')+'<circle cx="32" cy="32" r="20" fill="url(#'+id+')"/><path d="M14 25q18 12 35 4m-33 9q16 10 31 3" fill="none" stroke="#f3d3b94d" stroke-width="4"/><ellipse cx="32" cy="32" rx="31" ry="8" transform="rotate(-24 32 32)" fill="none" stroke="#c7bde7" stroke-width="4"/>';
  }
  if(seasonalObjectIds.includes(theme))body=holidayObject(theme,id);
  return `<svg viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">${body}</svg>`;
}

export function holidayScene(theme){
  const symbol=(x,y,size,index)=>holidaySymbol(theme,index).replace('<svg ',`<svg x="${x}" y="${y}" width="${size}" height="${size}" `);
  let body='';
  if(theme==='newyear')body='<path d="m118 6 30 40h-14l32 36h-18l34 40H54l34-40H70l32-36H88Z" fill="#398a6c"/><path d="m118 6 1 116H54l34-40H70l32-36H88Z" fill="#63ac8a"/><path d="m118 0 4 8 9 1-7 6 2 9-8-5-8 5 2-9-7-6 9-1Z" fill="#f1d381"/>'+Array.from({length:8},(_,i)=>`<circle class="seasonal-scene-light" style="--particle:${i}" cx="${89+i*17%58}" cy="${48+i*11%57}" r="4" fill="${i%2?'#f1c96c':'#cd6171'}"/>`).join('')+'<rect x="20" y="106" width="47" height="30" rx="3" fill="#bc4d64"/><path d="M40 106v30" stroke="#edc77a" stroke-width="7"/><rect x="154" y="99" width="49" height="37" rx="3" fill="#ce6270"/><path d="M177 99v37" stroke="#edc77a" stroke-width="8"/>';
  if(theme==='halloween')body=symbol(13,69,68,0)+symbol(76,55,80,0)+symbol(151,77,62,0)+'<path class="seasonal-scene-fly" d="m53 35-11-18 19 7 9-7 9 7 19-7-11 18-17-3ZM143 47l-8-14 15 5 7-5 7 5 15-5-8 14-14-3Z" fill="#b59fc5"/>';
  if(theme==='valentine')body='<g transform="rotate(-10 70 107)"><rect x="22" y="76" width="83" height="55" rx="4" fill="#edbba9"/><path d="m22 76 42 36 41-36" fill="#ffded0"/></g><g transform="rotate(12 163 105)"><rect x="115" y="65" width="85" height="60" rx="4" fill="#efc3b4"/><path d="m115 65 42 36 43-36" fill="#ffe4d6"/></g>'+symbol(47,4,45,0)+symbol(134,15,38,1);
  if(theme==='spring')body=Array.from({length:5},(_,i)=>`<g class="seasonal-scene-stem" style="--particle:${i};transform-origin:${32+i*38}px 138px"><path d="M${32+i*38} 138q-8-42 0-74" stroke="#86aa72" stroke-width="4"/><path d="M${32+i*38} 109q19-24 26-14-1 14-26 14" fill="#a0bd7b"/>${symbol(5+i*38,31+(i%2)*16,54,i)}</g>`).join('');
  if(theme==='space')body='<g class="seasonal-scene-fly" transform="rotate(18 120 70)"><path d="M99 101V49q0-30 21-42 21 12 21 42v52Z" fill="#d8dced"/><path d="m99 77-22 30v16l28-12 30 0 28 12v-16l-22-30" fill="#e5a572"/><circle cx="120" cy="53" r="13" fill="#7196c9" stroke="#a9bddc" stroke-width="5"/><path d="m105 111 15 29 15-29" fill="#ffd4a0"/></g>'+symbol(4,31,49,2)+symbol(162,53,57,1);
  if(seasonalObjectIds.includes(theme))body=symbol(12,74,60,0)+symbol(72,28,88,1)+symbol(158,78,58,2);
  return `<svg viewBox="0 0 230 145" fill="none" xmlns="http://www.w3.org/2000/svg">${body}</svg>`;
}
