import React from 'react';
export function calloutIcon(kind){
 const h=React.createElement,props={width:18,height:18,viewBox:'0 0 24 24','aria-hidden':true,fill:'currentColor'};
 if(kind==='tip')return h('svg',props,h('path',{d:'m12 2.8 2.8 5.7 6.3.9-4.6 4.5 1.1 6.3-5.6-3-5.6 3 1.1-6.3L3 9.4l6.2-.9Z'}));
 return h('svg',props,h('circle',{cx:12,cy:12,r:10}),kind==='success'?h('path',{d:'m7.5 12 3 3 6-6',fill:'none',stroke:'var(--callout-icon-ink,#fff)',strokeWidth:2,strokeLinecap:'round',strokeLinejoin:'round'}):h('path',{d:kind==='warning'?'M12 6.5v7 M12 16.5v.5':'M12 10.5v7 M12 6.5v.5',stroke:'var(--callout-icon-ink,#fff)',strokeWidth:2,strokeLinecap:'round'}));
}
