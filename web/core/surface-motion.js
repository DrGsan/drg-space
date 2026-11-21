// Keep layout and hit testing in their final position while content appears.
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const compactScreen = matchMedia('(max-width: 760px)');
const revealed = new WeakMap();
const running = new Set();
export const motionAllowed = () => !document.hidden && !reducedMotion.matches;

function stopMotion() {
  if (!motionAllowed()) for (const motion of running) motion.cancel();
}
reducedMotion.addEventListener('change', stopMotion);
document.addEventListener('visibilitychange', stopMotion);

function play(surface, id, frames, options) {
  const motion = surface.animate(frames, options);
  motion.id = id;
  running.add(motion);
  motion.finished.catch(()=>{}).finally(()=>running.delete(motion));
  return motion;
}

export function revealSurface(surface, screen) {
  if (!surface?.isConnected || revealed.get(surface)?.screen === screen) return;
  revealed.get(surface)?.motion?.cancel();
  const entry = {screen};
  revealed.set(surface, entry);
  if (screen === null || !motionAllowed()) return;
  entry.motion = play(surface, 'surface-entry', [
    {opacity:.68},
    {opacity:1},
  ], {duration:260, easing:'cubic-bezier(.22,1,.36,1)'});
  entry.motion.finished.catch(()=>{}).finally(()=>{entry.motion=undefined;});
}

export function enterDialog(panel, alwaysShift=false) {
  if (!motionAllowed()) return;
  const shift = alwaysShift || !compactScreen.matches;
  const start = shift ? {opacity:0, transform:'translateY(18px) scale(.985)'} : {opacity:0};
  const end = shift ? {opacity:1, transform:'none'} : {opacity:1};
  return play(panel, 'dialog-entry', [start,end], {duration:420, easing:'cubic-bezier(.22,1,.36,1)'});
}

export function exitDialog(panel, entrance, alwaysShift=false) {
  const {opacity,transform} = getComputedStyle(panel);
  entrance?.cancel();
  const shift = alwaysShift || !compactScreen.matches;
  const start = shift ? {opacity,transform} : {opacity};
  const end = shift ? {opacity:0, transform:'translateY(10px) scale(.99)'} : {opacity:0};
  return play(panel, 'dialog-exit', [start,end], {duration:180, easing:'cubic-bezier(.4,0,1,1)', fill:'forwards'});
}
