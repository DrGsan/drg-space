import {tText,locale} from "./i18n.js";
// Viewer-local presentation only. Never put these names in shared documents or awareness.
let account=null,personal={},people=new Map(),logins=new Map();
export function setPeopleNames(uid,value={}){
  const changed=account!==uid||JSON.stringify(personal)!==JSON.stringify(value||{});
  if(account!==uid){people.clear();logins.clear();}
  account=uid;personal={...(value||{})};return changed;
}
export function rememberPeople(values=[]){
  for(const person of values){
    if(!person?.id||!person.login)continue;
    const previous=people.get(person.id);
    if(previous?.login&&previous.login!==person.login)logins.delete(previous.login.toLowerCase());
    people.set(person.id,{...previous,...person});logins.set(person.login.toLowerCase(),person.id);
  }
}
export function knownPerson(person){
  if(!person)return {};
  const id=person.id||logins.get(person.login?.toLowerCase());
  return {...people.get(id),...person,...(id?{id}:{})};
}
export const personForLogin=login=>knownPerson({login});
export const personNickname=person=>personal[knownPerson(person).id]||'';
export function personName(person){const p=knownPerson(person);return personal[p.id]||p.display_name||p.login||tText("common:remoteUser");}
export const personSearch=person=>`${personName(person)} ${person?.display_name||''} ${person?.login||''}`.toLocaleLowerCase(locale());

export const namedPeople=()=>[...people.values()].filter(person=>personal[person.id]);
