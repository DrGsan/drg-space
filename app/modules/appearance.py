from app.i18n import message as localized_message
import json
import re
from typing import Literal
from app.http import Router, Request
from pydantic import BaseModel, ConfigDict, Field
from app.wallpaper_presets import PRESETS as WALLPAPER_PRESETS, catalog as wallpaper_catalog, default_wallpaper

THEMES = {'ocean':'Океан', 'sage':'Лес', 'amber':'Песок', 'lilac':'Лаванда', 'rose':'Роза', 'slate':'Графит'}
WALLPAPERS = WALLPAPER_PRESETS | {'dusk', 'mist', 'none'}
WALLPAPER_PATTERN = r'^(?:' + '|'.join(re.escape(value) for value in sorted(WALLPAPERS)) + r'|[a-f0-9]{32})$'
class Appearance(BaseModel):
    model_config = ConfigDict(extra='forbid')
    theme: str = 'ocean'
    mode: str = 'system'
    day_start: str = Field(default='07:00', pattern=r'^([01][0-9]|2[0-3]):[0-5][0-9]$')
    night_start: str = Field(default='22:00', pattern=r'^([01][0-9]|2[0-3]):[0-5][0-9]$')
    style: Literal['classic', 'glass'] = 'glass'
    seasonal: Literal['auto', 'off'] = 'auto'
    wallpaper: str = Field(default='dunes', pattern=WALLPAPER_PATTERN)

def new_account_appearance():
    return Appearance(wallpaper=default_wallpaper()).model_dump()

def preferences(settings):
    # Resolve defaults at the API boundary as well as in the web client, so
    # native clients receive the same appearance without rewriting the account.
    return {**Appearance().model_dump(),**settings.get('appearance',{})}

def create_appearance_router(db,ready,fail):
    router=Router()
    @router.get('/api/appearance')
    def get_appearance(request:Request):
        s=ready(request)
        return {'themes':THEMES,'preferences':preferences(json.loads(s['settings'])),
                'wallpaper_categories':wallpaper_catalog()}
    @router.put('/api/appearance')
    def save(data:Appearance,request:Request):
        s=ready(request)
        if data.theme not in THEMES or data.mode not in ('light','dark','schedule','system'):
            fail(422,localized_message('selectAThemeModeAndDifferentDayAnd'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            settings=json.loads(c.execute('SELECT settings FROM users WHERE id=?',(s['user_id'],)).fetchone()[0])
            # Native and older web clients send only the colour/schedule fields.
            # Keep the independently selected web style and wallpaper intact.
            merged=Appearance.model_validate({**settings.get('appearance',{}),**data.model_dump(exclude_unset=True)})
            if merged.day_start==merged.night_start:fail(422,localized_message('selectDifferentDayAndNightTimes'))
            if merged.wallpaper not in WALLPAPERS and not c.execute('SELECT 1 FROM media WHERE id=? AND user_id=? AND NOT EXISTS (SELECT 1 FROM note_assets a WHERE a.id=media.id)',(merged.wallpaper,s['user_id'])).fetchone():
                fail(422,localized_message('backgroundImageIsNotAvailableSelectItAgain'))
            settings['appearance']=merged.model_dump()
            c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),s['user_id']))
        return merged.model_dump()
    return router
