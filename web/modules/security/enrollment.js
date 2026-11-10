import {tText,tHtml} from "../../core/i18n.js";
import QRCode from 'qrcode';
import { $,esc,formBind } from '../../core/ui.js';

export function mountEnrollment(host,ctx,onActivated,finishLabel=tText("security:enable2Fa")){
  host.innerHTML=`<form data-enroll><label>${tHtml("security:verifyPassword")}<input name="password" type="password" autocomplete="current-password" maxlength="256" required></label><button class="primary" type="submit">${tHtml("security:setUpAuthenticator")}</button></form><div data-enroll-next></div>`;
  formBind($('[data-enroll]',host),async form=>{
    const result=await ctx.api('/2fa/enroll','POST',{password:form.get('password')});
    const qr=await QRCode.toDataURL(result.uri,{width:220,margin:1});
    if(!host.isConnected)return;
    $('[data-enroll]',host).hidden=true;
    $('[data-enroll-next]',host).innerHTML=`<img class="qr" src="${qr}" alt="${tHtml("security:qrCodeForSettingUpAuthenticator")}"><p class="hint">${tHtml("security:canTScanAddThisKeyManually")}</p><code class="secret">${esc(result.secret)}</code><form data-confirm><label>${tHtml("security:codeFromTheApp")}<input name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" required></label><button class="primary" type="submit">${tHtml("security:verifyCode")}</button></form>`;
    formBind($('[data-confirm]',host),async form=>{
      const result=await ctx.api('/2fa/confirm','POST',{code:form.get('code')});
      if(!host.isConnected)return;
      $('[data-enroll-next]',host).innerHTML=`<h3>${tHtml("security:saveRecoveryCodes")}</h3><p class="muted">${tHtml("security:eachCodeCanBeUsedOnceInsteadOf")}</p><pre class="recovery">${result.recovery_codes.map(esc).join('\n')}</pre><button class="secondary" data-download-codes>${tHtml("security:downloadCodes")}</button><form data-activate><label class="check-label"><input type="checkbox" name="saved" required>${tHtml("security:iHaveSavedTheRecoveryCodes")}</label><button class="primary" type="submit" disabled>${esc(finishLabel)}</button></form>`;
      $('[data-download-codes]',host).onclick=()=>{
        const url=URL.createObjectURL(new Blob([result.recovery_codes.join('\n')],{type:'text/plain;charset=utf-8'})),link=document.createElement('a');
        link.href=url;link.download='drg-recovery-codes.txt';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      };
      $('[name=saved]',host).onchange=event=>{$('[data-activate] button',host).disabled=!event.target.checked;};
      formBind($('[data-activate]',host),async form=>{
        if(!form.has('saved'))throw new Error(tText("security:confirmSavingRecoveryCodes"));
        const activated=await ctx.api('/2fa/activate','POST',{saved_recovery_codes:true});
        await onActivated(activated);
      });
    });
  });
}
