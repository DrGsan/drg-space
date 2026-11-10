import {tText} from "../../core/i18n.js";
export const templates = [
  { id: "blank", name: tText("notes:emptyPage"), icon: "📄", body: "" },
  {
    id: "journal",
    name: tText("notes:journal"),
    icon: "📔",
    body: tText("notes:todayWhatSImportantMainTaskThoughtsWrite"),
  },
  {
    id: "meeting",
    name: tText("notes:meetingNotes"),
    icon: "💬",
    body: tText("notes:participantsWhoAttendedTheMeetingAgendaFirstQuestion"),
  },
  {
    id: "project",
    name: tText("notes:project"),
    icon: "🚀",
    body: tText("notes:ideaWhatDoWeWantToCreateAnd"),
  },
];
