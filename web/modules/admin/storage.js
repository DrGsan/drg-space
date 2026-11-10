import {tText,tHtml} from "../../core/i18n.js";
import {bytes} from '../../core/ui.js';

// Notes are a subset of the database; they must never be added to the total again.
export function mountAdminStorage(host,ctx,result){
  const siteBytes=result.site_total_bytes;
  host.innerHTML=`<div class="admin-storage-summary"><span><strong>${result.users.length}</strong>${tHtml("admin:accounts2")}</span><span><strong data-storage-total>${bytes(siteBytes)}</strong><span data-storage-total-label>${tHtml("admin:onTheWebsitePhotoArchiveIsBeingLoaded")}</span></span></div><dl class="admin-storage-breakdown"><div><dt>${tHtml("admin:websiteFiles")}<small>${tHtml("admin:documentsVideosAndMessagesAttachmentsIncludingTheTrash")}</small></dt><dd>${bytes(result.file_bytes)}</dd></div><div><dt>${tHtml("admin:websiteImages")}<small>${tHtml("admin:uploadedPhotosAvatarsAndImagesInNotes")}</small></dt><dd>${bytes(result.media_bytes)}</dd></div><div><dt>${tHtml("admin:websiteDatabase")}<small>${tHtml("admin:recordsOfAllSectionsIndexesAndServiceData")}</small></dt><dd>${bytes(result.database_bytes)}</dd></div><div class="admin-storage-subset"><dt>${tHtml("admin:textAndNoteHistory")}<small>${tHtml("admin:alreadyInTheDatabaseImagesAccountedForAbove")}</small></dt><dd>${bytes(result.note_content_bytes)}</dd></div><div><dt>${tHtml("admin:familyPhotoArchive")}<small data-archive-storage-detail>${tHtml("admin:separateStorageForOriginalsPreviewsAndMessages")}</small></dt><dd><span data-archive-storage-value role="status">${tHtml("admin:loading")}</span><button type="button" class="text-link" data-archive-storage-retry hidden>${tHtml("admin:retry")}</button></dd></div></dl><p class="hint admin-storage-help">${tHtml("admin:totalSizeWebsiteDatabaseUploadedFilesAndSeparate")}</p>`;
  const value=host.querySelector('[data-archive-storage-value]'),detail=host.querySelector('[data-archive-storage-detail]'),retry=host.querySelector('[data-archive-storage-retry]'),total=host.querySelector('[data-storage-total]'),label=host.querySelector('[data-storage-total-label]');
  async function load(){
    retry.hidden=true;value.textContent=tText("admin:loading");
    try{
      const archive=await ctx.api('/family-photos/admin/storage','GET',undefined,true,{}, {timeoutMs:12000});
      if(!host.isConnected)return;
      if(!archive.configured){value.textContent=tText("admin:notConnected");detail.textContent=tText("admin:familyPhotosAreStoredInASeparateService");label.textContent=tText("admin:totalOnTheWebsite");return;}
      if(!archive.available)throw new Error(tText("admin:statisticsAreTemporarilyUnavailable"));
      value.textContent=bytes(archive.total_bytes);
      detail.textContent=tText("admin:originalsPreviewsMessagesAndAvatarsDatabaseDataMay", {value0:(bytes(archive.media_bytes)), value1:(bytes(archive.thumbnail_bytes)), value2:(bytes(archive.message_bytes+archive.avatar_bytes+archive.other_bytes)), value3:(bytes(archive.database_bytes))});
      total.textContent=bytes(siteBytes+archive.total_bytes);label.textContent=tText("admin:totalAccountedForWebsiteAndPhotoArchive");
    }catch{
      if(!host.isConnected)return;
      value.textContent=tText("admin:notAvailable");detail.textContent=tText("admin:unableToRetrievePhotoArchiveSize");retry.hidden=false;label.textContent=tText("admin:onTheWebsiteWithoutPhotoArchive");
    }
  }
  if(ctx.state.me.role!=='owner'){value.textContent=tText("admin:availableToOwner");label.textContent=tText("admin:onTheWebsiteWithoutPhotoArchive");return;}
  retry.onclick=load;load();
}
