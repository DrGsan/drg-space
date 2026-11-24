const views=new Set(['home','notes','tracker','tasks','chat','files','expenses','family_photos','social','settings','security','personal-data','admin']);
export function readSession(key){try{return sessionStorage.getItem(key);}catch{return null;}}
export function writeSession(key,value){try{if(value===null)sessionStorage.removeItem(key);else sessionStorage.setItem(key,value);}catch{}}
export function workspaceView(userId,mobile=false){const view=readSession((mobile?'drg-mobile-view:':'drg-workspace-view:')+userId);return views.has(view)?view:(mobile?'chat':'home');}
export function rememberWorkspace(userId,view,mobile=false){if(userId&&views.has(view))writeSession((mobile?'drg-mobile-view:':'drg-workspace-view:')+userId,view);}
