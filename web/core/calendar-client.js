// Public subscription data only. One in-flight request per year for all widgets.
const cache=new Map();
export function loadProductionCalendar(api,year,now=Date.now()){
  const found=cache.get(year);
  if(found?.pending)return found.pending;
  if(found&&found.until>now)return found.error?Promise.reject(found.error):Promise.resolve(found.data);
  const entry=found||{};
  entry.pending=api(`/calendar/holidays?year=${year}`).then(data=>{
    entry.error=null;entry.data=data;
    const next=Number(data.next_check_at)*1000;
    entry.until=data.refreshing?now+60000:Number.isFinite(next)?Math.max(now+60000,next):now+3600000;
    return data;
  }).catch(error=>{entry.until=now+60000;if(entry.data){entry.data={...entry.data,status:'stale'};return entry.data;}entry.error=error;throw error;}).finally(()=>{entry.pending=null;});
  cache.set(year,entry);
  // Browsing years must not grow a session cache without limit.
  if(cache.size>8)for(const [key,value] of cache){if(key!==year&&!value.pending){cache.delete(key);break;}}
  return entry.pending;
}
