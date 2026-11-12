// Small additions to the application's ISC-licensed Lucide icon vocabulary.
import {sidebarIcon} from '../../core/sidebar-icons.js';
const shapes={
 'trash':'<path d="M3 6h18M9 6V4h6v2M5 6l1 14h12l1-14M10 10v6m4-6v6"/>',
 'circle-x':'<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6m0-6-6 6"/>',
 'list':'<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
 'git-branch':'<path d="M6 3v12m0-6c7 0 12 0 12-6"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="3" r="2"/>',
 'table-2':'<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 9v12"/>',
 'sliders-horizontal':'<path d="M3 6h4m4 0h10M3 18h10m4 0h4M7 3v6m6 6v6M3 12h12m4 0h2m-6-3v6"/>',
 'chevron-right':'<path d="m9 6 6 6-6 6"/>',
 'zap':'<path d="m13 2-9 12h7l-1 8 10-12h-7z"/>',
 'square-check':'<rect x="3" y="3" width="18" height="18" rx="3"/><path d="m7 12 3 3 7-7"/>',
 'arrow-up':'<path d="M12 20V4m-6 6 6-6 6 6"/>',
 'arrow-down':'<path d="M12 4v16m-6-6 6 6 6-6"/>',
 'chevrons-up':'<path d="m6 12 6-6 6 6m-12 6 6-6 6 6"/>',
 'equal':'<path d="M5 9h14M5 15h14"/>',
 'minus':'<path d="M5 12h14"/>',
 'chevron-down':'<path d="m6 9 6 6 6-6"/>',
 'ellipsis':'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
 'external-link':'<path d="M15 3h6v6m0-6L11 13m-2-8H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>',
 'folder':'<path d="M3 7V5a2 2 0 0 1 2-2h5l3 3h6a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
 'plus':'<path d="M12 5v14M5 12h14"/>',
 'search':'<circle cx="11" cy="11" r="7"/><path d="m16 16 5 5"/>',
 'arrow-left':'<path d="m12 5-7 7 7 7M5 12h15"/>',
 'circle-check':'<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
 'bug':'<path d="M8 6 6 3m10 3 2-3M4 9H2m20 0h-2M4 15H2m20 0h-2M8 20l-2 2m10-2 2 2M12 8v13"/><rect x="5" y="6" width="14" height="15" rx="7"/>',
 'refresh-cw':'<path d="M20 7v5h-5M4 17v-5h5M5 7a8 8 0 0 1 13-2l2 2M4 17l2 2a8 8 0 0 0 13-2"/>',
 'file':'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Zm0 0v6h6"/>',
 'download':'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
 'network':'<rect x="9" y="2" width="6" height="6" rx="1"/><rect x="2" y="16" width="6" height="6" rx="1"/><rect x="16" y="16" width="6" height="6" rx="1"/><path d="M12 8v4M5 16v-4h14v4"/>',
 'user-round':'<circle cx="12" cy="8" r="5"/><path d="M3 22v-2a9 9 0 0 1 18 0v2"/>'
};
export function trackerIcon(name){return shapes[name]?`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${shapes[name]}</svg>`:sidebarIcon(name);}
