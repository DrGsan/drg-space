const statuses=['todo','work','review','done'];
export function normalizeFilters(value){
 const saved=value&&typeof value==='object'?value:{};
 return {query:typeof saved.query==='string'?saved.query.slice(0,300):'',mine:saved.mine===true,unfinished:saved.unfinished===true,
  statuses:[...new Set(Array.isArray(saved.statuses)?saved.statuses.filter(v=>statuses.includes(v)):[])],
  priorities:[...new Set(Array.isArray(saved.priorities)?saved.priorities.filter(v=>Number.isInteger(v)&&v>=0&&v<=3):[])],
  group:saved.group===''||typeof saved.group==='string'&&/^[A-Za-z0-9_-]{16}$/.test(saved.group)?saved.group:null};
}
export function matchesIssue(item,filters,uid,peer=''){
 return (!filters.mine||item.assignee_id===uid&&(item.assignee_peer||'')===peer)&&
  (!filters.unfinished||item.status!=='done')&&(!filters.statuses.length||filters.statuses.includes(item.status))&&
  (!filters.priorities.length||filters.priorities.includes(item.priority))&&
  (filters.group===null||(item.group_id||'')===filters.group)&&
  (!filters.query||[item.title,item.key,...item.tags||[]].join(' ').toLocaleLowerCase().includes(filters.query.toLocaleLowerCase()));
}
