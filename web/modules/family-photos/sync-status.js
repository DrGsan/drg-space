import {tText,tHtml,locale} from "../../core/i18n.js";
import {connectionMarkup,connectionDescription} from './connection-status.js';
import {esc} from '../../core/ui.js';

export const byteSize=value=>{
  const size=Math.max(0,Number(value)||0);
  const units=[tText("family_photos:b"),tText("family_photos:kb"),tText("family_photos:mb"),tText("family_photos:gb"),tText("family_photos:tb")];
  const index=Math.min(4,Math.floor(Math.log2(Math.max(1,size))/10));
  return `${(size/1024**index).toLocaleString(locale(),{maximumFractionDigits:index?1:0})} ${units[index]}`;
};
export const duration=value=>{
  const seconds=Math.max(1,Math.ceil(value));
  if(seconds<60)return tText("family_photos:sec", {value0:(seconds)});
  if(seconds<3600)return tText("family_photos:min", {value0:(Math.ceil(seconds/60))});
  const minutes=Math.ceil(seconds/60);
  return tText("family_photos:hours", {value0:(Math.floor(minutes/60)), value1:(minutes%60?(" "+tText("family_photos:min", {value0:(minutes%60)})+""):'')});
};
const stageLabel={metadata:tText("family_photos:receivingFileFromTelegram"),download:tText("family_photos:downloadingFromTelegram"),upload:tText("family_photos:savingFile"),
  restoring:tText("family_photos:readingSavedOriginal"),preview:tText("family_photos:preparingPreview")};

export function syncMarkup(status,group,{disclosure=false,open=false}={}){
  if(!status)return `<p class="hint">${group?.media_pending?tText("family_photos:checkingLoadingStatus"):group?tText("family_photos:checkingSynchronization"):tText("family_photos:groupsAreNotYetConnected")}</p>`;
  const current=status.current,batch=status.batch||{},pending=status.pending||0,pause=status.pause;
  const paused=Boolean(pause);
  const wait=current?Math.max(0,current.wait_until-status.server_time):0;
  const retry=Math.max(0,status.retry_at-status.server_time);
  const historyWait=Math.max(0,(status.history_wait_until||0)-status.server_time);
  const connection=status.connection||group?.connection;
  let title,description='';
  if(connection?.state==='migrated'||connection?.state==='access_lost'){
    title=connection.state==='migrated'?tText("family_photos:archiveOfTheConvertedGroup"):tText("family_photos:noAccessToTheGroup");description=connectionDescription(connection);
  }else if(status.state==='status_unavailable'){
    title=tText("family_photos:failedToUpdateLoadingStatus");description=tText("family_photos:checkingAgainSavedFilesAreAvailable");
  }else if(status.state==='login_required'){
    title=tText("family_photos:needToReEnterTelegram");description=tText("family_photos:administratorNeedsToReconnectTelegramSavedFilesAre");
  }else if(status.state==='offline'){
    title=tText("family_photos:connectionToTheUploaderIsLost");description=tText("family_photos:savedFilesAreAvailableLoadingWillContinueAfter");
  }else if(status.state==='unavailable'){
    title=tText("family_photos:telegramIsTemporarilyUnavailable");description=tText("family_photos:checkingAccessAgainSavedFilesRemainInPlace");
  }else if(paused){
    if(pause.kind==='invalid'){
      title=tText("family_photos:uploaderStoppedForVerification");description=tText("family_photos:failedToReadSavedRequestLimitsAdministratorNeeds");
    }else{
      title=pause.kind==='premium'?tText("family_photos:telegramHasLimitedDownloadSpeed"):tText("family_photos:telegramSynchronizationIsPaused");
      const remaining=Math.max(0,pause.until-status.server_time);
      const at=new Date(pause.until*1000).toLocaleString(locale(),{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
      description=tText("family_photos:nextAttemptViaTelegramRequestedAPauseWe", {value0:(at), value1:(duration(remaining)), value2:(duration(pause.seconds))});
      if(!pending){
        title=status.history_complete?tText("family_photos:allPhotosAndVideosHaveBeenUploaded"):tText("family_photos:theQueueForThisGroupIsEmpty");
        description=tText("family_photos:theQueueForThisGroupIsEmptyPreviously", {value0:(pause.scope==='all'?tText("family_photos:checkingForUpdatesIsWaitingForTheOverall"):tText("family_photos:uploadingNewFilesIsWaitingForTheOverall")), value1:(description)});
      }else if(pause.scope==='all') description=tText("family_photos:thisIsAGeneralTelegramConnectionPauseFor", {value0:(description)});
    }
  }else if(wait){
    title=tText("family_photos:telegramRequestedAWait");description=tText("family_photos:weWillContinueApproximately", {value0:(duration(wait)), value1:(current.bytes?tText("family_photos:theReceivedPartOfTheFileHasBeen"):tText("family_photos:telegramHasNotYetTransmittedTheDataFor"))});
  }else if(current){
    title=stageLabel[current.stage]||tText("family_photos:uploadingFile");
    if(['download','upload','restoring'].includes(current.stage)){
      description=`${byteSize(current.bytes)}${current.total?(" "+tText("family_photos:from", {value0:(byteSize(current.total))})+""):''}${current.speed?(" "+tText("family_photos:s", {value0:(byteSize(current.speed))})+""):''}`;
    }else description=current.stage==='preview'?tText("family_photos:fileSavedPreparingImageForGallery"):tText("family_photos:waitingForTelegramResponse");
  }else if(historyWait){
    title=tText("family_photos:telegramUpdateCheckIsPaused");description=tText("family_photos:weWillContinueApproximatelySavedPhotosAndVideos", {value0:(duration(historyWait))});
  }else if(status.state==='retry'){
    title=status.rate_limited?tText("family_photos:waitingForTelegramPermission"):tText("family_photos:reUploadingAfterError");
    description=tText("family_photos:filesRemainInTheQueueNoNeedTo", {value0:(retry?(""+tText("family_photos:nextAttemptApproximately", {value0:(duration(retry))})+" "):'')});
  }else if(pending){
    title=tText("family_photos:filesAreWaitingToBeUploaded");description=tText("family_photos:theUploaderIsProcessingTheFamilyGroupQueue");
  }else if(!status.history_complete){
    title=tText("family_photos:checkingTelegramHistory");description=tText("family_photos:photosAndVideosWillAppearAsTheyAre");
  }else{
    title=connection&&connection.state!=='working'?tText("family_photos:theFileQueueIsEmpty"):group?.kind==='export'?tText("family_photos:photosAndVideosHaveBeenUploadedFromTelegram"):tText("family_photos:allPhotosAndVideosHaveBeenUploaded");
    description=connection&&connection.state!=='working'?connectionDescription(connection):group?.kind==='export'?tText("family_photos:thisIsASavedCopyChangesInTelegram"):tText("family_photos:newMessagesPhotosAndVideosAreSynchronizedAutomatically");
  }
  if(status.stopped&&!pending&&!paused&&status.history_complete&&(!connection||connection.state==='working')){
    title=tText("family_photos:automaticQueueIsEmpty");
    description=tText("family_photos:savedPhotosAndVideosAreAvailableNewMessages");
  }
  const active=Boolean(current||pending||(!status.history_complete&&!paused&&status.state==='active'))&&!['migrated','access_lost','copy','export','disabled','deleting'].includes(connection?.state);
  const finish=html=>disclosure?`<details class="fp-sync-disclosure" data-fp-sync-details ${open?'open':''}><summary><span class="fp-sync-indicator ${active?'is-active':''}" aria-hidden="true"></span><span>${esc(title)}</span></summary><div class="fp-sync-popover">${html}</div></details>`:html;
  if(!active){
    const label=connection&&connection.state!=='working'?connectionMarkup({...group,connection}):esc(title);
    return finish(`<section class="fp-sync fp-sync-compact" aria-label="${tHtml("family_photos:familyPhotoSynchronization")}" role="status"><span data-fp-sync-title title="${esc(description)}">${label}</span>${disclosure?`<p class="hint">${esc(description)}</p>`:''}</section>`);
  }
  const percent=Math.max(0,Math.min(100,Number(batch.percent)||0));
  const fileLabel=current?`${{photo:tText("family_photos:photo"),video:tText("family_photos:video2"),animation:'GIF',sticker:tText("family_photos:sticker2")}[current.kind]||tText("family_photos:file")} · ${current.name||''}`:'';
  return finish(`<section class="fp-sync ${active?'is-active':''}" aria-label="${tHtml("family_photos:familyPhotoSynchronization")}">
    <div class="fp-sync-heading"><strong data-fp-sync-title>${esc(title)}</strong>${active&&batch.total?`<span>${esc(percent.toLocaleString(locale()))}%</span>`:''}</div>
    ${active&&batch.total?`<progress max="100" value="${percent}" aria-label="${tHtml("family_photos:currentQueueIsReady")}"></progress><div class="fp-sync-counts"><span>${tHtml("family_photos:readyOutOfRemaining", {value0:(batch.completed), value1:(batch.total), value2:(pending)})}</span><span>${status.eta_seconds!=null&&!wait&&!paused&&status.state==='active'?tText("family_photos:downloadingWithoutTelegramPauses", {value0:(duration(status.eta_seconds))}):status.state==='active'&&!wait&&!paused?tText("family_photos:remainingTimeIsBeingClarified"):''}</span></div>`:''}
    ${fileLabel?`<p class="fp-sync-file" title="${esc(fileLabel)}">${esc(fileLabel)}</p>`:''}
    <p class="hint fp-sync-description" role="status" title="${esc(description)}">${esc(description)}</p>
    ${current&&['download','upload','restoring'].includes(current.stage)&&!wait&&!paused?`<progress class="fp-sync-file-progress" max="${current.total||1}" ${current.total?`value="${Math.min(current.total,current.bytes)}"`:''} aria-label="${tHtml("family_photos:currentFileIsReady")}"></progress>`:''}

  </section>`);
}
