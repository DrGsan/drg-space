// Notion uses both <details> and ul.toggle for toggles. Preserve complete
// block trees before the generic list parser can turn their titles into bullets.
export function normalizeToggleHTML(html){
  const doc=new DOMParser().parseFromString(html,'text/html');
  let changed=false;
  const hasContent=element=>element.textContent.trim()||element.querySelector('img,hr,table,video,audio,[data-node-type="blockContainer"]');
  const containerFor=(title,children,type,heading)=>{
    const container=doc.createElement('div');container.dataset.nodeType='blockContainer';
    const content=doc.createElement('div');
    content.dataset.contentType=heading?'heading':type;
    if(heading){content.dataset.level=heading.tagName.slice(1);content.dataset.isToggleable='true';}
    const inline=doc.createElement('div');inline.className='bn-inline-content';
    inline.append(...title);content.append(inline);container.append(content);
    const group=doc.createElement('div');group.dataset.nodeType='blockGroup';group.append(...children);
    if(hasContent(group))container.append(group);
    changed=true;return container;
  };
  for(const element of [...doc.querySelectorAll('details')].reverse()){
    const summary=element.querySelector(':scope > summary');if(!summary)continue;
    const heading=summary.querySelector('h1,h2,h3');
    const title=[...(heading||summary).childNodes];summary.remove();
    const container=containerFor(title,[...element.childNodes],'toggleListItem',heading);
    const li=element.parentElement;
    if(li?.tagName==='LI'&&li.children.length===1&&!Array.from(li.childNodes).some(node=>node.nodeType===3&&node.textContent.trim()))li.replaceWith(container);else element.replaceWith(container);
  }
  for(const li of [...doc.querySelectorAll('li')].reverse()){
    const list=li.parentElement;
    const toggle=li.matches('.toggle,[data-block-type="toggle"],[data-type="toggle"]')||list?.matches('ul.toggle,ol.toggle,[data-block-type="toggle"],[data-type="toggle"]');
    // BlockNote groups nested lists, but not code/quotes inside a list item.
    const nodes=[...li.childNodes];
    const blockTags='ul,ol,pre,blockquote,div,p,table,hr,details';
    let firstChild=nodes.findIndex(node=>node.nodeType===1&&node.matches(blockTags));
    let title=firstChild<0?nodes:nodes.slice(0,firstChild);
    // A paragraph or plain div may wrap the title. Later paragraphs belong to
    // the body, and must not be merged into the clickable toggle label.
    const wrapper=nodes[firstChild];
    if(wrapper?.nodeType===1&&wrapper.matches('p,div')&&!wrapper.querySelector(blockTags)&&!title.some(node=>node.textContent.trim())){
      title=[...wrapper.childNodes];firstChild++;
    }
    if(!toggle&&firstChild<0)continue;
    if(!toggle&&!nodes.slice(firstChild).some(node=>node.nodeType===1&&!node.matches('ul,ol')))continue;
    const heading=toggle&&title.find(node=>node.nodeType===1&&node.matches('h1,h2,h3'));
    const checkbox=title.find(node=>node.nodeType===1&&node.matches('input[type="checkbox"]'));
    const inline=heading?[...heading.childNodes]:title.filter(node=>node!==checkbox);
    const type=toggle?'toggleListItem':checkbox?'checkListItem':list?.tagName==='OL'?'numberedListItem':'bulletListItem';
    const children=firstChild<0?[]:nodes.slice(firstChild);
    // Some clipboard producers put a nested list immediately after its li.
    let next=li.nextElementSibling;
    while(next?.matches('ul,ol')){children.push(next);next=next.nextElementSibling;}
    const container=containerFor(inline,children,type,heading);
    const content=container.firstElementChild;
    if(type==='checkListItem')content.dataset.checked=String(checkbox.checked);
    const start=Number(li.getAttribute('value')||(!li.previousElementSibling&&list?.getAttribute('start')));
    if(type==='numberedListItem'&&Number.isInteger(start)&&start>0)content.dataset.start=String(start);
    li.replaceWith(container);
  }
  return changed?doc.body.innerHTML:null;
}
