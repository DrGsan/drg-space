import {tHtml} from "./i18n.js";
import {$} from './ui.js';

export function modal(html, {motion = false} = {}) {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML =
    `<section class="modal" role="dialog" aria-modal="true"><button class="modal-close icon-btn" aria-label="${tHtml("common:close")}">×</button>${html}</section>`;
  document.body.append(overlay);
  const panel = $('.modal', overlay);
  const reduced = () => document.hidden || matchMedia('(prefers-reduced-motion: reduce)').matches;
  let panelMotion;
  if (motion && !reduced()) panelMotion = panel.animate([
    {opacity:0, transform:'translateY(18px) scale(.985)'},
    {opacity:1, transform:'none'},
  ], {duration:420, easing:'cubic-bezier(.22,1,.36,1)'});
  const prev = document.activeElement;
  let closed = false;
  const close = () => {
    if (closed) return;
    if (!overlay.dispatchEvent(new Event('beforeclose', {cancelable:true}))) return;
    closed = true;
    const remove = () => {
      const top = [...document.querySelectorAll('.modal-overlay')].at(-1) === overlay;
      overlay.remove();
      document.removeEventListener("keydown", key);
      if (top && prev?.isConnected) prev.focus({preventScroll:true});
      overlay.dispatchEvent(new Event("close"));
    };
    if (!motion || reduced()) { panelMotion?.cancel(); remove(); return; }
    // Start from the displayed frame when Escape interrupts the entrance.
    const current = getComputedStyle(panel), opacity = current.opacity, transform = current.transform;
    panelMotion?.cancel();
    panel.inert = true;
    panelMotion = panel.animate([
      {opacity, transform}, {opacity:0, transform:'translateY(10px) scale(.99)'},
    ], {duration:180, easing:'cubic-bezier(.4,0,1,1)', fill:'forwards'});
    panelMotion.finished.catch(()=>{}).finally(remove);
  };
  const key = (e) => {
    if ([...document.querySelectorAll(".modal-overlay")].at(-1)!==overlay)return;
    if (e.key === "Escape") close();
    if (e.key === "Tab") {
      const items = [
        ...overlay.querySelectorAll("button,input,select,textarea,a[href]"),
      ].filter((x) => !x.disabled && !x.hidden);
      const first = items[0],
        last = items.at(-1);
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last?.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first?.focus();
      }
    }
  };
  $(".modal-close", overlay).onclick = close;
  let outsidePointer=null;
  overlay.addEventListener('pointerdown',e=>{outsidePointer=e.target===overlay&&e.button===0?{id:e.pointerId,x:e.clientX,y:e.clientY}:null;});
  overlay.addEventListener('pointercancel',()=>outsidePointer=null);
  overlay.onclick = e => {
    const start=outsidePointer;outsidePointer=null;
    if(e.target===overlay&&start&&Math.hypot(e.clientX-start.x,e.clientY-start.y)<6)close();
  };
  document.addEventListener("keydown", key);
  $(".modal-close", overlay).focus({preventScroll:true});
  return overlay;
}
