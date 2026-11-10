// Status controls must not displace the common cover/icon/title layout.
export function statusBadges(host){
  let badges=host.querySelector('.note-status-badges');
  if(!badges){badges=document.createElement('div');badges.className='note-status-badges';badges.addEventListener('toggle',event=>{if(event.target.open)for(const item of badges.children)if(item!==event.target)item.open=false;},true);}
  const cover=host.querySelector('.page-cover-wrap:not([hidden])');
  const target=cover||host.querySelector('.page-decoration')||host;
  badges.classList.toggle('on-cover',Boolean(cover));target.append(badges);return badges;
}
