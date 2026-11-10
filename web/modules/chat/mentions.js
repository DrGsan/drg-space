import {locale} from "../../core/i18n.js";
import {personSearch} from '../../core/person-names.js';
// Only offer usernames at a word boundary and at the current caret, never emails.
export function mentionQuery(text,caret){
  const match=String(text).slice(0,caret).match(/(?:^|[^\p{L}\p{N}_@])@([\p{L}\p{N}_.-]{0,80})$/u);
  return match?{start:caret-match[1].length-1,end:caret,query:match[1].toLowerCase()}:null;
}
export function mentionCandidates(members,query){return members.filter(p=>personSearch(p).includes(query.toLocaleLowerCase(locale()))).slice(0,50);}
