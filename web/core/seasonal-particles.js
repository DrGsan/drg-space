// Shapes and CSS motion only: no per-particle timers or work during scrolling.
const shapes={
  snow:'<circle r="2.5" fill="#edf6ff"/>',
  stars:'<path d="m0-4 1 3 3 1-3 1-1 3-1-3-3-1Z" fill="#e8efff"/>',
  hearts:'<path d="M0 4-4 0C-8-5-2-7 0-3c2-4 8-2 4 3Z" fill="#e897b1"/>',
  petals:'<path d="M-4 4Q-7-4 4-4 7 4-4 4Z" fill="#f1b5cf"/>',
  leaves:'<path d="M-4 4Q-7-5 5-5 7 4-4 4Z" fill="#d9a05e"/><path d="m-5 6 8-9m-5 5-1-3m3 1h3" fill="none" stroke="#976c3f" stroke-width=".7"/>',
  notes:'<path d="M1-5v7" fill="none" stroke="#d8b96b" stroke-width="1.5"/><ellipse cx="-1" cy="2" rx="2.5" ry="1.8" fill="#d8b96b"/><path d="M1-5q5 1 3 4" fill="none" stroke="#d8b96b" stroke-width="1.5"/>',
  confetti:'<path d="M-2-4h4v8h-4Z"/>'
};
export const seasonalParticleLimit=24;
export function seasonalParticles(kind,width,height){
  if(!Object.hasOwn(shapes,kind))return '';
  const stars=kind==='stars',rising=kind==='hearts'||kind==='notes';
  return Array.from({length:seasonalParticleLimit},(_,i)=>{
    const y=stars?height*(i*17%100)/100:rising?height+20:-20;
    const color=kind==='confetti'?` fill="${['#ed9eb1','#e7c574','#87b9d2','#a3c4a0'][i%4]}"`:'';
    return `<g transform="translate(${width*(i*37%100)/100} ${y})"><g class="seasonal-ambient" data-particle-kind="${kind}" style="--particle:${i};--duration:${(kind==='snow'?14:9)+i%5}s"${color}>${shapes[kind]}</g></g>`;
  }).join('');
}
