// One capsule belongs to the navigation, rather than to each destination.
// Workspace rendering may replace the shell; preserve the bar and its current
// presentation position so a rapid second tap never restarts at the old tab.
const controllers=new WeakMap();
const barSelector='.workspace>.mobile-app-tabs:not(.drive-mobile-tabs)';
const signature=nav=>Array.from(nav.querySelectorAll('button[data-mobile-view]'),button=>button.dataset.mobileView).join('|');

export function captureMobileTabs(root){
  const element=root.querySelector(barSelector),controller=element&&controllers.get(element);
  const focused=element?.contains(document.activeElement)&&document.activeElement.matches(':focus-visible')?document.activeElement.dataset.mobileView:null;
  return {element,focused,presentation:controller?.presentation()};
}

export function restoreMobileTabs(root,snapshot,account){
  let nav=root.querySelector(barSelector);if(!nav)return;
  const previous=snapshot?.element;
  if(previous?.dataset.tabAccount===account&&signature(previous)===signature(nav)){
    const selected=nav.querySelector('[aria-current=page]')?.dataset.mobileView;
    previous.setAttribute('aria-label',nav.getAttribute('aria-label'));
    previous.querySelectorAll('button[data-mobile-view]').forEach(button=>{
      if(button.dataset.mobileView===selected)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');
    });
    nav.replaceWith(previous);nav=previous;
  }
  nav.dataset.tabAccount=account;
  let controller=controllers.get(nav);
  if(!controller){controller=mountIndicator(nav,root);controllers.set(nav,controller);}
  controller.update(nav===previous?snapshot.presentation:null);
  if(nav===previous&&snapshot.focused)Array.from(nav.querySelectorAll('button')).find(button=>button.dataset.mobileView===snapshot.focused)?.focus({preventScroll:true});
}

function mountIndicator(nav,root){
  const indicator=document.createElement('span');indicator.className='mobile-tab-indicator';indicator.setAttribute('aria-hidden','true');nav.prepend(indicator);
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');let positioned=false;
  function update(from=null){
    const button=nav.querySelector('button[aria-current=page]');
    if(!button?.offsetWidth||!nav.offsetWidth){nav.classList.remove('has-tab-indicator');positioned=false;return;}
    const continuing=from?.visible&&Math.abs(from.width-nav.offsetWidth)<1;
    if(!positioned||continuing||reduced.matches){
      indicator.style.transitionDuration='0s';
      indicator.style.transform=continuing?from.transform:`translateX(${button.offsetLeft}px)`;
    }
    indicator.style.width=button.offsetWidth+'px';indicator.style.height=button.offsetHeight+'px';indicator.style.top=button.offsetTop+'px';nav.classList.add('has-tab-indicator');
    if(!positioned||continuing||reduced.matches){indicator.getBoundingClientRect();indicator.style.removeProperty('transition-duration');}
    indicator.style.transform=`translateX(${button.offsetLeft}px)`;positioned=true;
  }
  const resize=new ResizeObserver(()=>{positioned=false;update();});resize.observe(nav);
  const motion=()=>{positioned=false;update();};reduced.addEventListener('change',motion);
  const removal=new MutationObserver(()=>{
    if(nav.isConnected)return;
    resize.disconnect();removal.disconnect();reduced.removeEventListener('change',motion);controllers.delete(nav);
  });removal.observe(root,{childList:true});
  return {update,presentation:()=>({visible:positioned&&Boolean(nav.offsetWidth),width:nav.offsetWidth,transform:getComputedStyle(indicator).transform})};
}
