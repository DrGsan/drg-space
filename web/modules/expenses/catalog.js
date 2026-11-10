import catalog from '../../../app/expense_categories.json' with {type:'json'};
import {language,tText} from '../../core/i18n.js';
export const expenseCategories=catalog.filter(x=>x.group!=='transfers');
export const categoryGroups=['food','transport','home','life','other'];
export const currencies=['RUB','USD','EUR'];
export const legacyCategoryNames={food:'food',beer:'beer',whisky:'whiskey',tinctures:'elixirs',cocktails:'cocktails',mixed:'everything'};
export function categoryName(id){const item=catalog.find(x=>x.id===id);return item?.[language()]||tText('expenses:'+(legacyCategoryNames[id]||'otherGroup'));}
export function categoryArt(id){const alias={food:'restaurant',beer:'alcohol',whisky:'alcohol',tinctures:'alcohol',cocktails:'alcohol',mixed:'other'};return '/assets/expense-categories/'+(alias[id]|| (catalog.some(x=>x.id===id)?id:'other'))+'.webp';}
