import {tText} from "./i18n.js";
export const spaceTemplates = {
  family: {label:tText("common:family"),icon:'house'},
  group: {label:tText("common:friends"),icon:'users-round'},
  project: {label:tText("common:project"),icon:'notebook-text'},
  organization: {label:tText("common:organization"),icon:'users-round'},
};

export function spaceTemplate(space){
  return spaceTemplates[space.template] || spaceTemplates.family;
}
