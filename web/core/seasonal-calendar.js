// Civil dates stay local to the device, including movable dates and year boundaries.
export function seasonalDate(theme,year,{preview=false}={}){
  if(theme.rule?.day_of_year)return new Date(year,0,theme.rule.day_of_year,12);
  if(theme.rule){const {month,weekday,nth}=theme.rule,first=new Date(year,month-1,1,12);return new Date(year,month-1,1+(weekday-first.getDay()+7)%7+7*(nth-1),12);}
  const [month,day]=(preview?theme.start:theme.date)||theme.start;
  return new Date(year,month-1,day,12);
}
export function seasonalThemesForDay(themes,date=new Date()){
  return themes.filter(theme=>{const day=seasonalDate(theme,date.getFullYear());return date.getMonth()===day.getMonth()&&date.getDate()===day.getDate();}).sort((a,b)=>(b.priority||0)-(a.priority||0));
}
export function sortedSeasonalThemes(themes,year=new Date().getFullYear()){
  return [...themes].sort((a,b)=>seasonalDate(a,year,{preview:true})-seasonalDate(b,year,{preview:true}));
}
export function seasonalThemeForDate(themes,date=new Date()){
  const day=(date.getMonth()+1)*100+date.getDate();
  return [...themes].sort((a,b)=>(b.priority||0)-(a.priority||0)).find(theme=>{
    if(theme.rule)return seasonalThemesForDay([theme],date).length;
    const start=theme.start[0]*100+theme.start[1],end=theme.end[0]*100+theme.end[1];
    return start<=end?day>=start&&day<=end:day>=start||day<=end;
  })?.id||'';
}
export function seasonalGreetingTheme(themes,date=new Date(),preview=''){
  const selected=themes.find(theme=>theme.id===preview);
  return selected||themes.find(theme=>theme.id===seasonalThemeForDate(themes,date));
}
export function nextSeasonalDateCheck(date=new Date()){
  return Math.max(1000,new Date(date.getFullYear(),date.getMonth(),date.getDate()+1).getTime()-date.getTime()+100);
}
