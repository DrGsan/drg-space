import {tHtml} from "./i18n.js";
import {esc} from './ui.js';
import {cachedAvatar} from './avatar-cache.js';
import {personAvatar} from './people.js';
import {sidebarIcon} from './sidebar-icons.js';
import {spaceTemplate} from './space-templates.js';
import './space-avatar.css';

export function spaceAvatar(space,me){
  if(space.kind==='friends')return personAvatar(space.members.find(m=>m.id!==me.id));
  return `<span class="person-avatar family-avatar" aria-label="${tHtml("common:photoOfWorkspace", {value0:(esc(space.name))})}">${space.avatar_url?`<img data-avatar-source="${esc(space.avatar_url)}" ${cachedAvatar(space.avatar_url)?`src="${esc(cachedAvatar(space.avatar_url))}"`:''} alt="">`:sidebarIcon(spaceTemplate(space).icon)}</span>`;
}
