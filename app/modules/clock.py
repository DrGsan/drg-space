"""Clock widget settings: portable IANA time zones, no server timer or new table."""
from app.i18n import message as localized_message
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_serializer, model_validator

class ClockCity(BaseModel):
    model_config=ConfigDict(extra='forbid')
    timezone:str=Field(default='local',min_length=1,max_length=100)
    label:str=Field(default='',max_length=100)

    @field_validator('timezone')
    @classmethod
    def supported_zone(cls,value):
        # Resolve on the device, never against the server's timezone.
        if value=='local':return value
        if value in ('localtime','posixrules','Factory'):raise ValueError(localized_message('selectTheCityOrUtcTimeZone'))
        try:ZoneInfo(value)
        except (ZoneInfoNotFoundError,ValueError):raise ValueError(localized_message('unknownTimeZoneForExampleEuropeMoscowOr')) from None
        return value

class ClockConfig(ClockCity):
    mode:Literal['analog','digital']='analog'
    theme:Literal['classic','minimal','station','roman','orbit','bold','segments','flip','accent']='classic'
    # Empty means the existing single clock; all cities survive size changes.
    cities:list[ClockCity]=Field(default_factory=list,max_length=4)
    show_seconds:StrictBool=True
    show_timezone:StrictBool=True
    show_date:StrictBool=True
    hour_format:Literal['24h','12h']='24h'
    date_format:Literal['dd.mm.yyyy','yyyy-mm-dd','dd/mm/yyyy','long','weekday','custom']='dd.mm.yyyy'
    date_template:str=Field(default='DD.MM.YYYY',min_length=1,max_length=80)

    @model_validator(mode='after')
    def primary_city(self):
        if self.cities:self.timezone,self.label=self.cities[0].timezone,self.cities[0].label
        return self

    @model_serializer(mode='wrap')
    def portable_single_clock(self,handler):
        data=handler(self)
        if not self.cities:data.pop('cities',None)
        return data
