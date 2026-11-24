import {tHtml, tText} from '../../core/i18n.js';
import {$} from '../../core/ui.js';
import {preferenceStatus} from '../../core/preferences.js';

export function mountNotesAssistant(panel, ctx) {
  const details = panel.closest('details');
  let loaded = false;
  async function load() {
    if (loaded || !details.open) return;
    loaded = true;
    try {
      const settings = await ctx.api('/admin/notes-assistant');
      if (!panel.isConnected) return;
      panel.innerHTML = `<form><p class="muted" data-ai-key-status></p><label>${tHtml('admin:notesAssistantAccount')}<select name="account"><option value="">${tHtml('admin:notesAssistantOff')}</option></select></label><button type="submit" class="primary">${tHtml('notes:save')}</button><p role="alert" data-ai-error></p></form>`;
      const form = $('form', panel);
      const account = form.elements.account;
      for (const user of settings.users) account.add(new Option(user.login, user.id));
      account.value = settings.user_id && settings.users.some(user => user.id === settings.user_id) ? settings.user_id : '';
      const updateStatus = user => preferenceStatus(panel, settings.configured && user ? tText('admin:notesAssistantOn') : tText('admin:notesAssistantOff'));
      $('[data-ai-key-status]', form).textContent = tText(settings.configured ? 'admin:notesAssistantKeySet' : 'admin:notesAssistantKeyMissing');
      updateStatus(settings.user_id);
      form.onsubmit = async event => {
        event.preventDefault();
        const button = $('button', form);
        const status = $('[data-ai-error]', form);
        button.disabled = true;
        status.textContent = '';
        try {
          const {user_id: user} = await ctx.api('/admin/notes-assistant', 'PUT', {user_id: account.value || null});
          ctx.state.me.notes_assistant_enabled = settings.configured && user === ctx.state.me.id;
          updateStatus(user);
          ctx.toast(tText('admin:notesAssistantSaved'));
        } catch (error) { status.textContent = error.message; }
        finally { button.disabled = false; }
      };
    } catch (error) { panel.textContent = error.message; loaded = false; }
  }
  details.addEventListener('toggle', load);
  load();
}
