import {tText,locale} from "../../core/i18n.js";
export const callLive=call=>call&&['ringing','connecting','active'].includes(call.status);
export function callDuration(seconds){seconds=Math.max(0,Math.floor(seconds||0));return `${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`;}
export function callLabel(call){
  if(call.status==='ringing')return call.outgoing?tText("chat:outgoingCall"):tText("chat:incomingCall");
  if(call.status==='connecting')return tText("chat:connecting");
  if(call.status==='active')return tText("chat:call");
  if(call.status==='missed')return call.outgoing?tText("chat:noAnswer"):tText("chat:missed");
  if(call.status==='declined')return call.outgoing?tText("chat:rejected"):tText("chat:rejected2");
  if(call.status==='cancelled')return call.outgoing?tText("chat:cancelled"):tText("chat:missed");
  if(call.status==='failed')return tText("chat:couldNotConnect");
  return call.outgoing?tText("chat:outgoing"):tText("chat:incoming");
}
export function callDate(stamp,now=Date.now()/1000){
  const value=new Date(stamp*1000),today=new Date(now*1000);
  return value.toDateString()===today.toDateString()?value.toLocaleTimeString(locale(),{hour:'2-digit',minute:'2-digit'}):value.toLocaleDateString(locale(),{day:'2-digit',month:'2-digit',year:'2-digit'});
}
