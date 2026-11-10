import {tText,tHtml} from "./i18n.js";
import {$,esc,formBind} from './ui.js';
import {personNickname,setPeopleNames} from './person-names.js';

export function editNickname(ctx,person,changed=()=>{}){
  if(!person?.id||person.id===ctx.state.me.id)return;
  const dialog=ctx.modal(`<h2>${tHtml("common:personalName")}</h2><p>${esc(person.display_name||person.login)} · @${esc(person.login)}</p><form data-personal-name><label>${tHtml("common:howToCallThisPerson")}<input name="nickname" maxlength="80" value="${esc(personNickname(person))}" placeholder="${tHtml("common:enterNickname")}" autocomplete="off"></label><p class="hint">${tHtml("common:thisNameIsOnlyVisibleToYouIt")}</p><button class="primary compact" type="submit">${tHtml("common:saveName")}</button><button class="text-link" type="button" data-reset-personal-name>${tHtml("common:removeNickname")}</button></form>`);
  async function save(value){
    const result=await ctx.api('/users/'+person.id+'/nickname',value?'PUT':'DELETE',value?{nickname:value}:undefined);
    ctx.state.me.people_names=result;setPeopleNames(ctx.state.me.id,result);
    $('.modal-close',dialog).click();document.dispatchEvent(new Event('drg-people-names-change'));await changed();ctx.toast(tText("common:personalNameSaved"));
  }
  formBind($('[data-personal-name]',dialog),async data=>save(data.get('nickname').trim()));
  $('[data-reset-personal-name]',dialog).onclick=async e=>{e.target.disabled=true;try{await save('');}catch(error){ctx.toast(error.message);e.target.disabled=false;}};
  $('input',dialog).focus();
}
