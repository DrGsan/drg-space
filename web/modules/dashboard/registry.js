import {tText} from "../../core/i18n.js";
import {mountFilesWidget} from './widgets/files.js';
import {mountFavorites} from './widgets/favorites.js';
import {mountAuthenticator} from './widgets/authenticator.js';
import {mountExpenseWidget} from './widgets/expenses.js';
import { mountClock } from "./widgets/clock.js";
import { clockDefaults } from "./widgets/clock-format.js";
import { mountChatWidget } from "./widgets/chat.js";
import { mountTasks } from "./widgets/tasks.js";
import {taskDefaults} from './widgets/task-source.js';
import { mountCalendar } from "./widgets/calendar.js";
import { mountWeather } from "./widgets/weather.js";
import { mountRecentNotes } from "./widgets/notes.js";
import { mountRss } from "./widgets/rss.js";
import { mountBookmarks } from "./widgets/bookmarks.js";
import { mountMedia } from "./widgets/media.js";
// A widget owns only its host. New widgets register here, without editing the shell.
export const widgetRegistry = new Map();
export function registerWidget(definition) {
  if (
    !/^[a-z][a-z0-9_-]{0,50}$/.test(definition.id) ||
    widgetRegistry.has(definition.id) ||
    !/^[a-z][a-z0-9_-]{0,50}$/.test(definition.moduleId || "") ||
    typeof definition.mount !== "function"
  )
    throw new Error("Invalid or duplicate widget registration");
  widgetRegistry.set(definition.id, definition);
}
registerWidget({
  id: "tasks",
  moduleId: "tasks",
  title: tText("dashboard:reminders"),
  size: "2x1",
  defaults:taskDefaults,
  mount:mountTasks,
});
registerWidget({
  id: "notes",
  moduleId: "notes",
  title: tText("dashboard:lastNotes"),
  size: "2x1",
  mount: mountRecentNotes,
});
registerWidget({
  id: "weather",
  moduleId: "weather",
  title: tText("dashboard:weather"),
  size: "1x1",
  className: "weather-panel",
  mount: mountWeather,
});
registerWidget({
  id: "calendar",
  moduleId: "calendar",
  title: tText("dashboard:calendar"),
  size: "1x1",
  className: "calendar-panel",
  mount: mountCalendar,
});

registerWidget({id:"rss",moduleId:"rss",title:tText("dashboard:rssNews"),size:"2x2",defaults:{feeds:[],limit:10},mount:mountRss});
registerWidget({id:"bookmarks",moduleId:"bookmarks",title:tText("dashboard:websiteShortcuts"),size:"1x1",defaults:{links:[]},mount:mountBookmarks});
registerWidget({id:"media",moduleId:"media",title:tText("dashboard:galleryAndGifs"),size:"1x1",defaults:{items:[],selected:[],mode:"static",interval:10,fit:"cover"},mount:mountMedia});

registerWidget({id:"chat",moduleId:"chat",title:tText("dashboard:myConversations"),size:"1x1",defaults:{space_id:null},mount:mountChatWidget});

registerWidget({id:"clock",moduleId:"clock",title:tText("dashboard:clocks"),size:"1x1",className:"clock-panel",defaults:clockDefaults,mount:mountClock});

registerWidget({id:'favorites',moduleId:'favorites',title:tText("dashboard:favouriteNotes"),size:'2x1',mount:mountFavorites});
registerWidget({id:'authenticator',moduleId:'authenticator',title:tText("dashboard:label2faCodes"),size:'1x1',defaults:{entry_ids:[]},mount:mountAuthenticator});
registerWidget({id:'expenses',moduleId:'expenses',title:tText("dashboard:splitBills"),size:'1x1',defaults:{participants:[],retention_days:30},mount:mountExpenseWidget});

registerWidget({id:'files',moduleId:'files',title:tText("dashboard:files"),size:'1x1',mount:mountFilesWidget});
