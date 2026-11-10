"""Module catalog and per-account switches, separate from widget placement."""
from app.i18n import message as localized_message
import json
from app.i18n import message, SystemLabel
from app.http import Router, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

MODULES = {
    'tracker': {'title':'Задачи','kind':'module','default_enabled':False,'description':'Проекты, доски, задачи и совместная работа. Выполненные задачи сохраняются.'},
    'clock': {'title':'Часы','kind':'widget','default_enabled':True,'description':'Аналоговые или цифровые часы, регионы, подписи, три темы и дата.'},
    'files': {'title':'Файлы','kind':'module','default_enabled':True,'description':'Приватные файлы и папки, редактор и публикации по ссылке.'},
    'tasks': {'title':'Напоминания','kind':'module','default_enabled':True,'description':'Список напоминаний и отметки о выполнении.'},
    'favorites': {'title':'Главные заметки','kind':'widget','requires':'notes','default_enabled':True,'description':'Быстрые ссылки на ваши избранные страницы.'},
    'calendar': {'title':'Календарь','kind':'widget','default_enabled':True,'description':'Мини-календарь на главной и в напоминаниях. Работает и без напоминаний.'},
    'weather': {'title':'Погода','kind':'widget','default_enabled':True,'description':'Погодная сводка и выбор города.'},
    'rss': {'title':'Новости RSS','kind':'widget','default_enabled':True,'description':'Заголовки из RSS и Atom, несколько источников в одном блоке.'},
    'chat': {'title':'Чат','kind':'module','default_enabled':True,'description':'Сообщения контактам и беседы пространств в реальном времени.'},
    'bookmarks': {'title':'Ярлыки сайтов','kind':'widget','default_enabled':True,'description':'Ссылки на любимые сайты в виде плиток.'},
    'notes': {'title':'Заметки','kind':'module','default_enabled':True,'description':'Страницы, редактор и последние заметки на главной.'},
    'media': {'title':'Галерея и GIF','kind':'widget','default_enabled':True,'description':'Изображение, анимация или слайд-шоу выбранных картинок.'},
    'authenticator': {'title':'Коды 2FA','kind':'widget','default_enabled':True,'description':'Коды внешних сервисов. Требуется 2FA вашего аккаунта.'},
    'expenses': {'title':'Раздельный счёт','kind':'module','default_enabled':True,'description':'Расходы вечера, взаимные долги и временные ссылки на итоги.'},
}

SECTION_MODULES = frozenset(mid for mid, spec in MODULES.items() if spec['kind'] == 'module')

def section_preferences(settings):
    """Persist switches only for sections; old widget switches describe placement."""
    return {mid: value for mid, value in settings.get('modules', {}).items() if mid in SECTION_MODULES or mid == 'family_photos'}

def legacy_widget_flags(settings):
    return {mid: value for mid, value in settings.get('modules', {}).items() if mid in MODULES and MODULES[mid]['kind'] == 'widget'}


class ModulePreferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: dict[str, StrictBool] = Field(max_length=100)

def module_enabled(settings, module_id):
    spec = MODULES.get(module_id)
    if spec is None:return False
    if spec.get('requires'):return module_enabled(settings, spec['requires'])
    if spec['kind'] == 'widget':return True
    return settings.get('modules', {}).get(module_id, spec['default_enabled'])

def create_module_services(db, ready, fail, integration=None):
    router = Router()

    def payload(settings, session):
        result = {'catalog': [{'id': mid, **spec} for mid, spec in MODULES.items()],
                  'enabled': {mid: module_enabled(settings, mid) for mid in MODULES}}
        optional = integration.module(session, settings) if integration else None
        if optional:
            result['catalog'].append(optional)
            result['enabled']['family_photos'] = settings.get('modules',{}).get('family_photos') is True
        return result

    def guard(module_id):
        if module_id not in MODULES:
            raise ValueError('Unregistered module')
        def check(request):
            s = ready(request)
            if not module_enabled(json.loads(s['settings']), module_id):
                required = MODULES[module_id].get('requires', module_id)
                fail(403, message('moduleDisabled', module=required, title=SystemLabel(MODULES[required]['title'])))
            return s
        return check

    @router.get('/api/modules')
    def get_modules(request: Request):
        s = ready(request)
        return payload(json.loads(s['settings']), s)

    @router.put('/api/modules')
    def save_modules(data: ModulePreferences, request: Request):
        s = ready(request)
        optional = {'family_photos'} if integration else set()
        if set(data.enabled) - MODULES.keys() - optional:
            fail(422, localized_message('unknownModule'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            settings = json.loads(c.execute('SELECT settings FROM users WHERE id=?', (s['user_id'],)).fetchone()[0])
            # Validate the latest grant under the same lock as the switch update.
            s = dict(s, settings=json.dumps(settings))
            if 'family_photos' in data.enabled:
                integration.validate_switch(s, data.enabled['family_photos'])
            if legacy_widget_flags(settings):
                from app.modules.widget_models import dashboard, default_dashboard
                adapted = dashboard(settings)
                if adapted != settings.get('dashboard', default_dashboard()):
                    adapted['version'] += 1
                settings['dashboard'] = adapted
            settings['modules'] = section_preferences(settings) | {mid: value for mid, value in data.enabled.items() if mid in SECTION_MODULES or (integration and mid == 'family_photos')}
            if settings['modules'].get('family_photos') is not True or (integration and not integration.allowed(s)):
                settings['modules'].pop('family_photos', None)
            c.execute('UPDATE users SET settings=? WHERE id=?', (json.dumps(settings), s['user_id']))
        return payload(settings, s)

    return router, guard
