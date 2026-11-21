import './section-transition.css';

let activeTransition;

export function transitionSection(update,layoutChanged) {
  activeTransition?.skipTransition();
  if(document.hidden||matchMedia('(prefers-reduced-motion: reduce)').matches||!document.startViewTransition){
    update();
    return;
  }
  const {classList}=document.documentElement;
  classList.add('section-switching');
  classList.toggle('section-switching-layout',layoutChanged);
  const transition=document.startViewTransition(update);
  activeTransition=transition;
  transition.ready.catch(()=>{});
  transition.finished.catch(()=>{}).finally(()=>{
    if(activeTransition!==transition)return;
    activeTransition=null;
    classList.remove('section-switching','section-switching-layout');
  });
  return transition.updateCallbackDone;
}
