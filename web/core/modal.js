import {tHtml} from "./i18n.js";
import {$} from './ui.js';
import {enterDialog,exitDialog,motionAllowed} from './surface-motion.js';

export function modal(html, {motion = true} = {}) {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML =
    `<section class="modal" role="dialog" aria-modal="true"><button class="modal-close icon-btn" aria-label="${tHtml("common:close")}">×</button>${html}</section>`;
  document.body.append(overlay);
  const panel = $('.modal', overlay);
  const panelMotion = motion ? enterDialog(panel) : undefined;
  const prev = document.activeElement;
  let closed = false;
  const close = () => {
    if (closed) return;
    if (!overlay.dispatchEvent(new Event('beforeclose', {cancelable:true}))) return;
    closed = true;
    const top = topModal() === overlay;
    overlay.dataset.closing = '';
    overlay.inert = true;
    overlay.style.pointerEvents = 'none';
    document.removeEventListener("keydown", key);
    if (top && prev?.isConnected) prev.focus({preventScroll:true});
    overlay.dispatchEvent(new Event("close"));
    const remove = () => overlay.remove();
    if (!motion || !motionAllowed()) { panelMotion?.cancel(); remove(); return; }
    // Start from the displayed frame when Escape interrupts the entrance.
    const exit = exitDialog(panel,panelMotion);
    exit.finished.catch(()=>{}).finally(remove);
  };
  const topModal = () => [...document.querySelectorAll(".modal-overlay:not([data-closing]):not([inert])")].at(-1);
  const key = (e) => {
    if (topModal()!==overlay)return;
    if (e.key === "Escape") close();
    if (e.key === "Tab") {
      const items = [
        ...overlay.querySelectorAll("button,input,select,textarea,a[href]"),
      ].filter((x) => !x.disabled && x.tabIndex >= 0 && !x.closest('[inert]') && x.getClientRects().length);
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
