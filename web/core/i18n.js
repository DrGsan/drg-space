import i18next from 'i18next';
import configuration from '../../locales/languages.json' with {type:'json'};
import resources from '../../locales/resources.js';

export const languages = configuration.languages;
export const fallbackLanguage = configuration.defaultLanguage;
const storageKey = 'drg-language';
export function normalizeLanguage(value) {
  const base = String(value || '').toLowerCase().replaceAll('_', '-').split('-')[0];
  return Object.hasOwn(languages, base) ? base : null;
}
export function systemLanguage(preferences = globalThis.window?.navigator ? (navigator.languages || [navigator.language]) : [fallbackLanguage]) {
  return preferences.map(normalizeLanguage).find(Boolean) || fallbackLanguage;
}
export function languagePreference() {
  try { const value = localStorage.getItem(storageKey); return value === 'system' || Object.hasOwn(languages, value) ? value : 'system'; }
  catch { return 'system'; }
}
export function resolveLanguage(preference = languagePreference()) {
  return preference === 'system' ? systemLanguage() : normalizeLanguage(preference) || fallbackLanguage;
}
const instance = i18next.createInstance();
instance.init({resources, lng: resolveLanguage(), fallbackLng: fallbackLanguage,
  supportedLngs: Object.keys(languages), ns: Object.keys(resources[fallbackLanguage]), defaultNS: 'common',
  initImmediate: false, initAsync: false, interpolation: {escapeValue: false,alwaysFormat:true,format:(value,format,lng)=>typeof value==='number'?new Intl.NumberFormat(languages[normalizeLanguage(lng)||fallbackLanguage].locale).format(value):value}, returnNull: false});
export const language = () => instance.resolvedLanguage || fallbackLanguage;
export const locale = () => languages[language()].locale;
export const tText = (key, parameters = {}) => instance.t(key, parameters);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
// For existing HTML templates only: static translated text is escaped, while
// slots retain the markup/escaping of the original expression. User text in
// slots must continue to use esc(), just as it does outside translated phrases.
export function tHtml(key, parameters = {}) {
  const slots = [], masked = {};
  for (const [name, value] of Object.entries(parameters)) {
    if (name === 'count') masked.count = value;
    else { masked[name] = `\uE000${slots.length}\uE001`; slots.push(typeof value==='number'?formatNumber(value):String(value ?? '')); }
  }
  return escapeHTML(tText(key, masked)).replace(/\uE000(\d+)\uE001/g, (_, index) => slots[Number(index)] ?? '');
}
export function storeLanguagePreference(value) {
  if (value !== 'system' && !Object.hasOwn(languages, value)) throw new Error('Unsupported language');
  try { localStorage.setItem(storageKey, value); } catch {}
}
export function applyDocumentLanguage() {
  if (!globalThis.document?.documentElement) return;
  document.documentElement.lang = language();
  document.cookie = `drg_language=${language()}; Path=/; SameSite=Lax${location.protocol==='https:'?'; Secure':''}`;
  document.documentElement.dir = languages[language()].direction;
  const manifest=document.querySelector('link[rel=manifest]');
  if(manifest)manifest.href='/assets/manifests/'+language()+'.webmanifest';
}
applyDocumentLanguage();
export function accountLanguageChanged(settings) {
  const preference = Object.hasOwn(languages, settings?.language) ? settings.language : 'system';
  const previous = language();
  storeLanguagePreference(preference);
  return resolveLanguage(preference) !== previous;
}
export function translateSystemText(value) {
  if (typeof value !== 'string' || language() === fallbackLanguage) return value;
  const source = resources[fallbackLanguage].server || {};
  const key = Object.keys(source).find(key => source[key] === value);
  return key ? tText('server:' + key) : value;
}
export const formatNumber = (value, options = {}) => new Intl.NumberFormat(locale(), options).format(value);

export function translateStaticPage(root = document) {
  for (const element of root.querySelectorAll('[data-i18n]')) element.textContent = tText(element.dataset.i18n);
  for (const element of root.querySelectorAll('[data-i18n-content]')) element.setAttribute('content', tText(element.dataset.i18nContent));
}
