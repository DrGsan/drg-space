import {tText} from '../../core/i18n.js';
// The application shell can manage navigation and drafts without downloading
// React/BlockNote. Entry points load the editor before mounting editable views.
export {pageLinkIds,subpagePlaceholder} from './block-utils.js';
let runtime,pending;
export function loadEditor(){
  if(runtime)return Promise.resolve(runtime);
  return pending??=import('./blocks.js').then(value=>runtime=value).catch(error=>{pending=null;throw new Error(tText('notes:editorLoadFailed'),{cause:error});});
}
function loaded(){if(!runtime)throw new Error('Editor runtime has not loaded');return runtime;}
export const mountBlockEditor=(...args)=>loaded().mountBlockEditor(...args);
export const pageBlocks=(...args)=>loaded().pageBlocks(...args);
export const seedCollaborativeDoc=(...args)=>loaded().seedCollaborativeDoc(...args);
