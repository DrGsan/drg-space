import {tText} from "../../core/i18n.js";
// Keep calculations in integer kopecks. Blank fields share the remainder.
export function kopecks(value){
  if(!/^\d+(?:[.,]\d{1,2})?$/.test(String(value).trim()))throw Error(tText("expenses:enterTheAmountToTheNearestCent"));
  const [rub,fraction='']=String(value).trim().replace(',','.').split('.');
  const result=Number(rub)*100+Number(fraction.padEnd(2,'0'));
  if(!Number.isSafeInteger(result)||result>100000000)throw Error(tText("expenses:amountIsTooLarge"));
  return result;
}
export function allocate(amount,ids,manual={}){
  if(!ids.length)throw Error(tText("expenses:selectTheParticipantsOrDebtor"));
  const shares={},automatic=[];let fixed=0;
  for(const id of ids){const value=manual[id];if(value==null||String(value).trim()==='')automatic.push(id);else{shares[id]=kopecks(value);fixed+=shares[id];}}
  const remainder=amount-fixed;
  if(remainder<0)throw Error(tText("expenses:theSpecifiedSharesExceedThePaymentAmount"));
  if(!automatic.length&&remainder!==0)throw Error(tText("expenses:sharesDoNotMatchThePaymentLeaveOne"));
  automatic.forEach((id,i)=>shares[id]=Math.floor(remainder/automatic.length)+(i<remainder%automatic.length?1:0));
  return shares;
}
export function allocateWeighted(amount,ids,weights={}){
  if(!ids.length||new Set(ids).size!==ids.length)throw Error(tText('expenses:selectTheParticipantsOrDebtor'));
  const values=ids.map(id=>weights[id]??1);
  if(values.some(n=>!Number.isInteger(n)||n<1||n>20))throw Error(tText('expenses:weightedInvalid'));
  const total=values.reduce((a,b)=>a+b,0),shares={},remainders=[];let used=0;
  ids.forEach((id,index)=>{shares[id]=Math.floor(amount*values[index]/total);used+=shares[id];remainders.push({id,index,remainder:amount*values[index]%total});});
  remainders.sort((a,b)=>b.remainder-a.remainder||a.index-b.index).slice(0,amount-used).forEach(({id})=>shares[id]++);
  return shares;
}
