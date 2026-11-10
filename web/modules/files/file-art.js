import {esc} from '../../core/ui.js';

export function fileArtwork(file,category,prefix='/files'){
 if(category==='folder')return `<svg class=drive-folder-art viewBox="0 0 64 52" aria-hidden=true><path class=drive-folder-back d="M4 14V9a4 4 0 0 1 4-4h14a6 6 0 0 1 4.2 1.8L30 10h26a4 4 0 0 1 4 4v29a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4Z"/><rect class=drive-folder-front x=4 y=17 width=56 height=30 rx="4"/><path class=drive-folder-seam d="M4 18h56"/></svg>`;
 const extension=file.name.includes('.')?file.name.split('.').at(-1).toUpperCase().slice(0,5):'',label=extension||({code:'CODE',archive:'ZIP',sheet:'XLS',image:'IMG',video:'VIDEO',audio:'AUDIO'})[category]||'';
 const marks=category==='sheet'?'<path d="M17 31h30v27H17Zm0 9h30m-30 9h30M27 31v27m10-27v27"/>':category==='image'?'<path d="m17 48 10-12 10 10 5-6 6 8M43 31h.1"/>':category==='audio'?'<path d="M18 38v12m6-20v28m7-34v40m7-34v28m7-20v12"/>':category==='video'?'<path d="m26 31 16 12-16 12Z"/>':category==='code'?'<path d="m24 32-8 10 8 10m16-20 8 10-8 10m-5-25-6 30"/>':'<path d="M17 31h30M17 37h30M17 43h26M17 49h30M17 55h22"/>';
 const paper=`<svg class="drive-document-art document-${category}" viewBox="0 0 64 80" aria-hidden=true><path class=drive-paper d="M12 3h29l14 14v56a4 4 0 0 1-4 4H12a4 4 0 0 1-4-4V7a4 4 0 0 1 4-4Z"/><path class=drive-paper-fold d="M41 3v11a3 3 0 0 0 3 3h11"/><g class=drive-paper-lines>${marks}</g>${label?`<text class=drive-paper-label x=32 y=69 text-anchor=middle>${esc(label)}</text>`:''}</svg>`;
 const preview=category==='image'&&file.id&&prefix!==false?`<img class=drive-thumbnail data-drive-thumbnail loading=lazy decoding=async src="/api${esc(prefix)}/${encodeURIComponent(file.id)}/thumbnail?v=${Number(file.version)||1}" alt="">`:'';
 return paper+preview;
}
