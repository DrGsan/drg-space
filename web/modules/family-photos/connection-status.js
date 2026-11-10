import {tText,tHtml} from "../../core/i18n.js";
import {esc,date} from '../../core/ui.js';

const labels={working:tText("family_photos:working"),migrated:tText("family_photos:groupConverted"),access_lost:tText("family_photos:noAccess"),unavailable:tText("family_photos:couldNotVerify"),offline:tText("family_photos:noConnection"),checking:tText("family_photos:checkingConnection"),paused:tText("family_photos:telegramPause"),login_required:tText("family_photos:telegramLoginRequired"),disabled:tText("family_photos:disabled"),deleting:tText("family_photos:waitingToDelete"),copy:tText("family_photos:copy"),export:tText("family_photos:offlineArchive")};
export const connectionLabel=connection=>connection?.hidden&&connection.state==='copy'?tText("family_photos:copyHidden"):connection?.hidden&&connection.state==='export'?tText("family_photos:offlineArchiveHidden"):labels[connection?.state]||tText("family_photos:checkingConnection");
export const connectionDescription=connection=>({
  migrated:tText("family_photos:groupConvertedToSupergroupOldArchiveSavedNew"),
  access_lost:tText("family_photos:synchronizationAccountLostAccessToTheGroupSaved"),
  unavailable:tText("family_photos:couldNotVerifySourceWillRetryAutomaticallySaved"),
  offline:tText("family_photos:noConnectionToTheSynchronizationProcessSavedArchive"),
  checking:tText("family_photos:waitingForCurrentVerificationOfThisGroupIn"),
  login_required:tText("family_photos:needToRestoreTheSynchronizationAccountInTelegram"),
  paused:tText("family_photos:telegramTemporarilyPausedRequests", {value0:(connection?.wait_until?(" "+tText("family_photos:nextAttempt", {value0:(date(connection.wait_until))})+""):'')}),
  disabled:tText("family_photos:groupHiddenSynchronizationStopped"),deleting:tText("family_photos:groupHiddenAndAwaitingDeletion"),
  copy:tText("family_photos:savedCopyOfTheGroupNewMessagesAre"),export:tText("family_photos:savedOfflineArchiveNewMessagesAreNotSynchronized"),
}[connection?.state]||'');
export function botLabel(group){
  if(group.connection?.state==='migrated')return tText("family_photos:sendingOldArchive");
  if(group.copy_created||group.kind==='export')return tText("family_photos:sendingReadOnly");
  return group.can_send?tText("family_photos:sendingAvailable"):({not_member:tText("family_photos:botIsNotAvailableInTheGroup"),restricted:tText("family_photos:theBotDoesNotHaveSendingPermissions"),not_configured:tText("family_photos:botIsNotConnected"),invalid_token:tText("family_photos:needToVerifyTheBotToken"),unsupported_group:tText("family_photos:sendingOnlySupergroups"),checking:tText("family_photos:verifyingBotPermissions"),paused:tText("family_photos:sendingPauseTelegram"),not_allowed:tText("family_photos:sendingIsNotAllowed")}[group.bot_reason]||tText("family_photos:couldNotVerifyBot"));
}
export function checkedMarkup(connection){
  return (connection?.checked_at?`<small class="admin-table-secondary fp-connection-time">${tHtml("family_photos:verified", {value0:(esc(date(connection.checked_at)))})}</small>`:'')+(connection?.last_success?`<small class="admin-table-secondary fp-connection-time">${tHtml("family_photos:lastSuccessfulVerification", {value0:(esc(date(connection.last_success)))})}</small>`:'');
}
export function targetMarkup(connection,{chat=false}={}){
  const id=connection?.migrated_group_id;
  if(!id)return '';
  return chat?`<a class="text-link" href="/#chat/telegram:${esc(id)}">${tHtml("family_photos:openNewConversation")}</a>`:`<button type="button" class="text-link" data-fp-target-group="${esc(id)}">${tHtml("family_photos:openNewGroup")}</button>`;
}
export function connectionMarkup(group,{chat=false}={}){
  const connection=group?.connection;if(!connection)return '';
  return `<span class="fp-connection" data-fp-connection="${esc(connection.state)}" title="${esc(connectionDescription(connection))}">${esc(connectionLabel(connection))}${targetMarkup(connection,{chat})}</span>`;
}
