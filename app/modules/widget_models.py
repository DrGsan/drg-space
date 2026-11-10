"""Fresh dashboard format: ordered independent widget instances."""
from app.i18n import message as localized_message
import copy
import json
from urllib.parse import urlsplit
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from app.modules.preferences import MODULES, legacy_widget_flags
from app.modules.clock import ClockConfig

ID_PATTERN = r'^[a-z][a-z0-9_-]{0,60}$'

def web_url(value):
    if not isinstance(value, str) or len(value) > 2000 or any(ord(c) < 33 for c in value) or '\\' in value:
        raise ValueError(localized_message('invalidWebsiteAddress'))
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(localized_message('aFullHttpOrHttpsAddressIsRequired2'))
    if parsed.port not in (None, 80, 443):
        raise ValueError(localized_message('standardPorts80And443AreAllowed'))
    return value

class Options(BaseModel):
    model_config = ConfigDict(extra='forbid')

class CalendarConfig(Options):
    production: StrictBool = True
    thematic: StrictBool = True

class FavoritesConfig(Options):
    columns: Literal['auto','1','2','3'] = 'auto'

class AuthenticatorConfig(Options):
    entry_ids:list[str]=Field(default_factory=list,max_length=100)

class ExpenseConfig(Options):
    participants:list[str]=Field(default_factory=list,max_length=28)
    retention_days:int=Field(default=30)

class ChatConfig(Options):
    space_id: str | None = Field(default=None,pattern=r'^[a-zA-Z0-9_-]{16}$')

class TasksConfig(Options):
    view: Literal['list','today','all','flagged'] = 'list'
    list_id: str | None = Field(default=None,pattern=r'^[a-zA-Z0-9_-]{8,80}$')

    @model_validator(mode='after')
    def list_only(self):
        if self.view!='list':self.list_id=None
        return self

class WeatherConfig(Options):
    city: str = Field(default='', max_length=100)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

class Feed(Options):
    label: str = Field(default='', max_length=100)
    url: str = Field(max_length=2000)

class RssConfig(Options):
    feeds: list[Feed] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=10, ge=1, le=50)

class Bookmark(Options):
    title: str = Field(min_length=1, max_length=100)
    url: str = Field(max_length=2000)
    icon: str = Field(default='auto', max_length=16)
    color: str = 'blue'

class BookmarkConfig(Options):
    links: list[Bookmark] = Field(default_factory=list, max_length=40)

class MediaItem(Options):
    id: str = Field(pattern=ID_PATTERN)
    title: str = Field(default='', max_length=100)
    asset_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    url: str | None = Field(default=None, max_length=2000)

class MediaConfig(Options):
    items: list[MediaItem] = Field(default_factory=list, max_length=30)
    selected: list[str] = Field(default_factory=list, max_length=30)
    mode: str = 'static'
    interval: int = Field(default=10, ge=2, le=86400)
    fit: str = 'cover'

class WidgetInstance(Options):
    id: str = Field(pattern=ID_PATTERN)
    module_id: str = Field(pattern=ID_PATTERN)
    title: str = Field(default='', max_length=100)
    hidden: StrictBool = False
    size: str = '1x1'
    config: dict = Field(default_factory=dict)

class DashboardLayout(Options):
    instances: list[WidgetInstance] = Field(max_length=30)
    version: int = Field(ge=1)

def default_dashboard():
    # Portable starting layout. Account-specific images, links, services and
    # conversations are deliberately configured by their owner after signup.
    widgets = [
        ('clock', '1x1', ClockConfig().model_dump()),
        ('files', '1x1', {}),
        ('tasks', '2x2', {}),
        ('favorites', '2x1', FavoritesConfig().model_dump()),
        ('calendar', '1x1', {}),
        ('weather', '1x1', WeatherConfig().model_dump()),
        ('rss', '2x2', RssConfig().model_dump()),
        ('chat', '1x1', ChatConfig().model_dump()),
        ('bookmarks', '1x1', BookmarkConfig().model_dump()),
        ('notes', '2x1', {}),
        ('media', '2x2', MediaConfig().model_dump()),
        ('authenticator', '1x1', AuthenticatorConfig().model_dump()),
        ('expenses', '1x1', ExpenseConfig().model_dump()),
    ]
    return {'version': 1, 'instances': [
        WidgetInstance(id=f'home_{mid}', module_id=mid, size=size, config=config).model_dump()
        for mid, size, config in widgets
    ]}

def dashboard(settings):
    layout = settings['dashboard'] if 'dashboard' in settings else default_dashboard()
    hidden = {mid for mid, enabled in legacy_widget_flags(settings).items() if enabled is False}
    if not hidden:return layout
    # Old switches hid every existing instance. Preserve that visibility without
    # mutating saved settings; newly submitted instances use their explicit flag.
    layout = copy.deepcopy(layout)
    for item in layout['instances']:
        if item['module_id'] in hidden:item['hidden'] = True
    return layout

def validate_layout(data, c, user_id, fail):
    if len(json.dumps(data.model_dump()).encode()) > 250000:
        fail(422, localized_message('widgetSettingsAreTooLarge'))
    ids = [item.id for item in data.instances]
    if len(set(ids)) != len(ids):
        fail(422, localized_message('widgetIdentifiersAreRepeated'))
    models = {'calendar':CalendarConfig,'tasks':TasksConfig,'favorites':FavoritesConfig,'authenticator':AuthenticatorConfig,'expenses':ExpenseConfig,'clock': ClockConfig, 'chat': ChatConfig, 'weather': WeatherConfig, 'rss': RssConfig, 'bookmarks': BookmarkConfig, 'media': MediaConfig}
    for item in data.instances:
        if item.module_id not in MODULES or item.size not in ('compact','wide','1x1','2x1','1x2','2x2','2x3') or (item.size=='2x3' and item.module_id!='rss'):
            fail(422, localized_message('unknownModuleOrWidgetSize'))
        try:
            if item.module_id in models:
                config = models[item.module_id].model_validate(item.config)
                if isinstance(config,AuthenticatorConfig):
                    if len(set(config.entry_ids))!=len(config.entry_ids):raise ValueError(localized_message('selectedServicesAreRepeated'))
                    for eid in config.entry_ids:
                        if not c.execute('SELECT 1 FROM authenticators WHERE id=? AND user_id=?',(eid,user_id)).fetchone():fail(404,localized_message('authenticatorServiceNotFound'))
                if isinstance(config,ExpenseConfig) and (config.retention_days not in (7,30,90) or any(not n.strip() or len(n)>80 for n in config.participants)):
                    raise ValueError(localized_message('enterNamesUpTo80CharactersAndA'))
                if isinstance(config, ChatConfig) and config.space_id and not c.execute('SELECT 1 FROM space_members WHERE space_id=? AND user_id=?',(config.space_id,user_id)).fetchone():
                    fail(404,localized_message('conversationNotFound'))
                if isinstance(config,TasksConfig) and config.list_id and not c.execute('SELECT 1 FROM task_lists l WHERE l.id=? AND (l.owner_id=? OR (EXISTS (SELECT 1 FROM task_list_members m WHERE m.list_id=l.id AND m.user_id=?) AND EXISTS (SELECT 1 FROM friendships f WHERE (f.first_id=l.owner_id AND f.second_id=?) OR (f.second_id=l.owner_id AND f.first_id=?))))',(config.list_id,user_id,user_id,user_id,user_id)).fetchone():
                    fail(404,localized_message('reminderListNotFound'))
                if isinstance(config, WeatherConfig) and ((config.latitude is None) != (config.longitude is None)):
                    raise ValueError(localized_message('bothCoordinatesAreRequiredForTheCity'))
                if isinstance(config, RssConfig):
                    for feed in config.feeds: web_url(feed.url)
                if isinstance(config, BookmarkConfig):
                    for link in config.links:
                        web_url(link.url)
                        if not link.title.strip() or link.color not in ('blue','sage','amber','lilac','rose','slate'):
                            raise ValueError(localized_message('enterTheNameAndColorOfTheLabel'))
                if isinstance(config, MediaConfig):
                    if config.mode not in ('static','slideshow') or config.fit not in ('cover','contain'):
                        raise ValueError(localized_message('invalidGalleryMode'))
                    item_ids = [asset.id for asset in config.items]
                    if len(set(item_ids)) != len(item_ids) or len(set(config.selected)) != len(config.selected) or not set(config.selected).issubset(item_ids):
                        raise ValueError(localized_message('selectExistingImagesWithoutDuplicates'))
                    for asset in config.items:
                        if bool(asset.url) == bool(asset.asset_id):
                            raise ValueError(localized_message('enterALinkOrUploadedFile'))
                        if asset.url:
                            web_url(asset.url)
                            if urlsplit(asset.url).scheme!='https':
                                raise ValueError(localized_message('forAnExternalImageAHttpsLinkIs'))
                        if asset.asset_id and not c.execute('SELECT 1 FROM media WHERE id=? AND user_id=?', (asset.asset_id,user_id)).fetchone():
                            fail(404, localized_message('imageNotFound'))
                item.config = config.model_dump()
            elif item.config:
                raise ValueError(localized_message('thisModuleCurrentlyHasNoAdditionalParameters'))
        except ValueError as error:
            fail(422, str(error))

def owned_widget(c, user_id, widget_id, module_id, fail):
    row = c.execute('SELECT settings FROM users WHERE id=?', (user_id,)).fetchone()
    instance = next((item for item in dashboard(json.loads(row[0]))['instances'] if item['id'] == widget_id and item['module_id'] == module_id), None)
    if not instance: fail(404, localized_message('widgetNotFound'))
    return instance
