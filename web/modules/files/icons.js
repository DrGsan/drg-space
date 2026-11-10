const paths={
 folderPlus:'M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2ZM12 10v8m-4-4h8',
 file:'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8ZM14 2v6h6M8 13h8M8 17h6',
 recent:'M12 8v4l3 2M21 12a9 9 0 1 1-9-9 9 9 0 0 1 9 9',
 back:'m14 5-7 7 7 7',chevron:'m9 5 7 7-7 7',down:'m8 10 4 4 4-4',
 list:'M8 5h13M8 12h13M8 19h13M3 5h.01M3 12h.01M3 19h.01',
 grid:'M3 3h6v6H3ZM15 3h6v6h-6ZM3 15h6v6H3ZM15 15h6v6h-6Z',
 rename:'m16 3 5 5-13 13H3v-5ZM14 5l5 5',move:'M14 2H6v8m8-8v6h6M14 2l6 6v13h-7M2 17h10m-4-4 4 4-4 4',
 properties:'M4 5h16M4 10h16M4 15h10M4 20h7',
 link:'m10 13 4-4M8 16l-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2 1 1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0',
 lock:'M6 10V7a6 6 0 0 1 12 0v3M4 10h16v11H4ZM12 14v3',
 preview:'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12ZM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
 image:'M3 3h18v18H3ZM3 17l6-6 4 4 3-3 5 5M16 7h.01',
 video:'M3 5h18v14H3ZM10 9l5 3-5 3Z',audio:'M9 18V5l11-2v13M9 5l11-2M9 18a3 3 0 1 1-3-3 3 3 0 0 1 3 3M20 16a3 3 0 1 1-3-3 3 3 0 0 1 3 3',
 archive:'M4 3h16v18H4ZM10 3v3h4v3h-4v3h4v6h-4v-3h4',
 sheet:'M3 3h18v18H3ZM3 9h18M3 15h18M9 3v18M15 3v18',code:'m8 7-5 5 5 5m8-10 5 5-5 5M14 3l-4 18',check:'m5 12 4 4L19 6',close:'m5 5 14 14M19 5 5 19'
};
const cloud='M7.5 17H6a4 4 0 0 1-.6-7.96 6.8 6.8 0 0 1 12.9-1.4A4.7 4.7 0 0 1 18 17h-1.5';
const drawings={
 shared:'<path d="M6 3h12M5 6h14M4 10l2-2h12l2 2v9H4Z"/><path d="M4 12h16"/><circle cx="7" cy="16" r="1"/>',
 upload:`<path d="${cloud}M12 22V11m-3.4 3.4L12 11l3.4 3.4"/>`,
 download:`<path d="${cloud}M12 10v12m-3.4-3.4L12 22l3.4-3.4"/>`,
 cloud:'<path d="M6 18h11.8a4.7 4.7 0 0 0 .5-9.36A6.8 6.8 0 0 0 5.4 10.04 4 4 0 0 0 6 18Z"/>',
 more:'<circle cx="12" cy="12" r="9.25"/><g fill="currentColor" stroke="none"><circle cx="7.8" cy="12" r="1.15"/><circle cx="12" cy="12" r="1.15"/><circle cx="16.2" cy="12" r="1.15"/></g>',
 folder:'<path d="M3 8V5.8C3 4.8 3.8 4 4.8 4H10l2 2h7.2c1 0 1.8.8 1.8 1.8v11.4c0 1-.8 1.8-1.8 1.8H4.8c-1 0-1.8-.8-1.8-1.8V8Zm0 1h18"/>',
 share:'<path d="M12 15V2.5m-3.5 3.6L12 2.5l3.5 3.6M8.5 8.5H6.2c-.7 0-1.2.5-1.2 1.2v10.1c0 .7.5 1.2 1.2 1.2h11.6c.7 0 1.2-.5 1.2-1.2V9.7c0-.7-.5-1.2-1.2-1.2h-2.3"/>',
 mail:'<rect x="2.5" y="5.5" width="19" height="13" rx=".7"/><path d="m3 6 9 7.5L21 6M3 18l5.7-5M21 18l-5.7-5"/>',
 trash:'<path d="M4.5 6.5h15M9 6.5V4h6v2.5M6 6.5l.7 13.2c0 .7.6 1.3 1.3 1.3h8c.7 0 1.3-.6 1.3-1.3L18 6.5M9.5 9.5v8M14.5 9.5v8M8 2.5h8"/>',
 moreFilled:'<circle cx="12" cy="12" r="10" fill="currentColor" stroke="none"/><g fill="var(--drive-dot-ink,#fff)" stroke="none"><circle cx="7.8" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="16.2" cy="12" r="1.2"/></g>',
 folderPlus:'<path d="M3 9V6.2c0-1 .8-1.8 1.8-1.8H10l2 2h4M3 9h12M3 9v10.2c0 1 .8 1.8 1.8 1.8h14.4c1 0 1.8-.8 1.8-1.8V15"/><circle cx="19" cy="7" r="4.2"/><path d="M19 4.7v4.6M16.7 7h4.6"/>',
 layoutChevron:'<path d="m8 8 4-4 4 4m-8 8 4 4 4-4"/>'
};
export const icon=name=>`<svg class="drive-icon" data-drive-icon="${name}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.45" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${drawings[name]||`<path d="${paths[name]||paths.file}"/>`}</svg>`;
