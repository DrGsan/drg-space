// Reconcile generated, escaped markup without recreating unchanged media.
// Keys belong to siblings; moving a node is only necessary when its order changes.
const rendered = new WeakMap();
const keys = ['data-output-id', 'data-guest-output-id', 'data-room', 'data-message-id', 'data-chat-day', 'data-chat-attachment-id', 'data-chat-search-results', 'data-person-presence', 'data-inbox-id',
  'data-guest-person', 'data-fp-group', 'data-fp-media-id', 'data-fp-message-id', 'data-fp-chat-file', 'data-fp-animation', 'data-fp-day', 'data-fp-pending', 'data-media-row', 'data-fp-source-message-id', 'id'];
function key(node) {
  if (node.nodeType !== 1) return null;
  for (const attribute of keys) if (node.hasAttribute(attribute)) return attribute + ':' + node.getAttribute(attribute);
  return null;
}
function sameType(a, b) {
  return a.nodeType === b.nodeType && (a.nodeType !== 1 || a.tagName === b.tagName && a.namespaceURI === b.namespaceURI);
}
function patch(node, next) {
  if (node.nodeType !== 1) {
    if (node.nodeValue !== next.nodeValue) node.nodeValue = next.nodeValue;
    return;
  }
  // The recording player owns its live source, progress and playback controls.
  // A changed URL/state/metadata still replaces it after revocation or refresh.
  if(node.hasAttribute('data-recording') && node.getAttribute('data-recording-identity') === next.getAttribute('data-recording-identity') && node.querySelector('[data-recording-player]'))return;
  // The canvas player owns this subtree. Keep a loaded animation through polls.
  if(node.hasAttribute('data-fp-animation')&&node.getAttribute('data-fp-animation')===next.getAttribute('data-fp-animation')&&node.getAttribute('data-animation-url')===next.getAttribute('data-animation-url')&&node.querySelector('canvas'))return;
  // A reply jump briefly highlights its existing article; background updates
  // must not dismiss that highlight before its timer finishes.
  if (node.classList.contains('is-highlighted') && next.classList.contains('chat-message')) next.classList.add('is-highlighted');
  for (const attribute of [...node.attributes]) if (!next.hasAttribute(attribute.name)) node.removeAttribute(attribute.name);
  for (const attribute of next.attributes) if (node.getAttribute(attribute.name) !== attribute.value) node.setAttribute(attribute.name, attribute.value);
  children(node, [...next.childNodes]);
}
function children(parent, wanted) {
  const keyed = new Map([...parent.childNodes].map(node => [key(node), node]).filter(([value]) => value !== null));
  let cursor = parent.firstChild;
  for (const next of wanted) {
    const value = key(next);
    let node = value === null ? cursor : keyed.get(value);
    if (!node || key(node) !== value || !sameType(node, next)) node = next;
    if (node !== cursor) parent.insertBefore(node, cursor);
    if (node !== next) patch(node, next);
    cursor = node.nextSibling;
  }
  while (cursor) {
    const next = cursor.nextSibling;
    cursor.remove();
    cursor = next;
  }
}
export function renderMarkup(parent, html) {
  if (rendered.get(parent) === html) return false;
  const template = document.createElement('template');
  template.innerHTML = html;
  children(parent, [...template.content.childNodes]);
  rendered.set(parent, html);
  return true;
}
