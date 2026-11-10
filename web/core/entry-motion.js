import {tHtml} from './i18n.js';
import {brand} from './brand.js';
import './entry-motion.css';

let transition;
export const entryTransitionActive=()=>Boolean(transition);
export const entryTransitionFinished=()=>transition?.finished.catch(()=>{})??Promise.resolve();
// Let the browser interpolate the old and new geometry on one timeline.
// Only DOM capture is awaited; requests never wait for the animation to end.
export function transitionEntry(update) {
  if(document.hidden||matchMedia('(prefers-reduced-motion: reduce)').matches||!document.startViewTransition){update();return Promise.resolve();}
  transition?.skipTransition();
  const current=document.startViewTransition(update);transition=current;
  current.ready.catch(()=>{});
  current.finished.catch(()=>{}).finally(()=>{if(transition===current)transition=null;});
  return current.updateCallbackDone;
}

// Keep the real input (including recovery codes and OS autofill) in charge.
export function mountAuthMotion(layout) {
  const form=layout.querySelector('form'),input=form.querySelector('[name=code]');
  const visibility=()=>layout.classList.toggle('motion-paused',document.hidden);
  visibility();document.addEventListener('visibilitychange',visibility);
  const update=()=>{
    if(layout.dataset.motionState==='error')layout.dataset.motionState='idle';
    const count=/^\d{0,6}$/.test(input?.value||'')?(input?.value.length||0):0;
    layout.querySelectorAll('[data-code-step]').forEach((step,i)=>{
      step.classList.toggle('is-filled',i<count);step.classList.toggle('is-current',i===count);
    });
  };
  input?.addEventListener('input',update);update();
  form.addEventListener('drg-auth-close',()=>{
    document.removeEventListener('visibilitychange',visibility);
    input?.removeEventListener('input',update);
  },{once:true});
  return state=>{
    if(!layout.isConnected)return;
    layout.dataset.motionState=state;
    form.setAttribute('aria-busy',String(state==='checking'));
    if(state==='error'&&!matchMedia('(prefers-reduced-motion: reduce)').matches){
      form.animate([{opacity:1},{opacity:.75},{opacity:1}],{duration:650,easing:'ease-in-out'});
    }
  };
}

export const codeProgress=()=>'<div class="auth-code-progress" aria-hidden="true">'+Array.from({length:6},()=>'<i data-code-step></i>').join('')+'</div>';

const loadingVisibility=()=>document.querySelector('.workspace-loading')?.classList.toggle('motion-paused',document.hidden);
document.addEventListener('visibilitychange',loadingVisibility);

export function mountWorkspaceLoading(root) {
  // A verified sign-in keeps its form and spinner while the workspace loads.
  // The dashboard will show placeholders in its final position, without an
  // intervening full-screen skeleton that flashes on a fast connection.
  if(root.querySelector('.auth-layout'))return;
  root.querySelector('#auth-form')?.dispatchEvent(new Event('drg-auth-close'));
  root.innerHTML=`<main class="workspace-loading" aria-busy="true"><div class="entry-loading-brand">${brand()}</div><div class="workspace-loading-body"><div class="workspace-loading-center"><div class="workspace-loading-art" aria-hidden="true"><img src="/assets/icons/drg-d8-logo.png" width="72" height="72" alt=""><div class="workspace-loading-beam"><i></i></div></div><div class="workspace-loading-status" role="status"><h1>${tHtml('app:loadingWorkspace')}</h1><p>${tHtml('app:loadingWorkspaceHint')}</p></div></div></div></main>`;
  loadingVisibility();
}
