import {tText} from "../../core/i18n.js";
// Legacy names remain readable; no stored layouts need conversion.
export const widgetSize = size => ({compact:'1x1',wide:'2x1'}[size] || size || '1x1');
export function sizeOptions(moduleId){
  const labels={'1x1':tText("dashboard:label11Square"),'2x1':tText("dashboard:label21Wide"),'1x2':tText("dashboard:label12Tall"),'2x2':tText("dashboard:label22Large"),'2x3':tText("dashboard:label23SixSquares")};
  return Object.entries(labels).filter(([key])=>key!=='2x3'||moduleId==='rss').map(([key,label])=>`<option value="${key}">${label}</option>`).join('');
}
