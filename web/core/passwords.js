import {tText} from "./i18n.js";
export function requireStrongPassword(value){
  if(value.length<12||value.length>256||!/[\p{Ll}]/u.test(value)||!/[\p{Lu}]/u.test(value)||!/[\p{Nd}]/u.test(value))
    throw new Error(tText("common:passwordAtLeast12CharactersLowercaseAndUppercase"));
}
