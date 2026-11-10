import {languages, languagePreference, storeLanguagePreference, tText, locale} from '../../core/i18n.js';
import {esc, toast} from '../../core/ui.js';

export function languageOptions(value) {
  return `<option value="system" ${value==='system'?'selected':''}>${esc(tText('localization:system'))}</option>` + Object.entries(languages).map(([id, language]) => `<option value="${id}" ${value===id?'selected':''}>${esc(language.name)}</option>`).join('');
}
export function mountLanguageSettings(host, {api, state}, beforeChange) {
  const value=state.me.settings.language || 'system';
  host.innerHTML=`<h2>${esc(tText('localization:language'))}</h2><p class="muted">${esc(tText('localization:accountHint'))}</p><form data-language-form><label>${esc(tText('localization:language'))}<select name="language">${languageOptions(value)}</select></label><p class="hint">${esc(tText('localization:systemHint'))}</p><p class="hint">${esc(tText('localization:formatExample',{date:new Intl.DateTimeFormat(locale(),{dateStyle:'long'}).format(new Date()),number:new Intl.NumberFormat(locale()).format(12345.67)}))}</p><button class="primary" type="submit">${esc(tText('localization:save'))}</button></form>`;
  host.querySelector('form').onsubmit=async event=>{
    event.preventDefault();const button=host.querySelector('button');button.disabled=true;
    try {
      if(!await beforeChange())return;
      const selected=host.querySelector('select').value;
      await api('/language','PUT',{language:selected});
      storeLanguagePreference(selected);location.reload();
    } catch(error){toast(error.message);} finally {button.disabled=false;}
  };
}
export function mountPublicLanguage(host) {
  if(!host||host.querySelector('[data-public-language]'))return;
  const label=document.createElement('label');label.className='public-language';label.dataset.publicLanguage='';
  label.innerHTML=`<span>${esc(tText('localization:language'))}</span><select aria-label="${esc(tText('localization:language'))}">${languageOptions(languagePreference())}</select>`;
  label.querySelector('select').onchange=event=>{storeLanguagePreference(event.target.value);location.reload();};host.append(label);
}
