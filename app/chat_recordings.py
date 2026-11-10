"""Explicit recording metadata beside immutable private chat attachments."""
import json
from typing import Annotated,Literal
from pydantic import BaseModel,ConfigDict,Field,ValidationError,model_validator
from app.i18n import message

SCHEMA='''CREATE TABLE IF NOT EXISTS chat_recordings(file_id TEXT PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,payload TEXT NOT NULL);'''
MAX_VOICE_MS=600000
MAX_VIDEO_NOTE_MS=60000


class Recording(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    kind:Literal['voice','video_note']
    duration_ms:int=Field(ge=300,le=MAX_VOICE_MS)
    waveform:list[Annotated[int,Field(strict=True,ge=0,le=31)]]=Field(default_factory=list,max_length=64)

    @model_validator(mode='after')
    def limits(self):
        if self.kind=='video_note' and (self.duration_ms>MAX_VIDEO_NOTE_MS or self.waveform):raise ValueError('Invalid video note metadata')
        return self


def validate(value,mime):
    recording=Recording.model_validate(value)
    from app.chat_storage import VIDEO_MIMES
    allowed=mime in ('audio/mp4','audio/webm','audio/ogg') if recording.kind=='voice' else mime in VIDEO_MIMES
    if not allowed:raise ValueError('Recording does not match attachment MIME')
    return recording


def request_payload(request,fail):
    value=request.headers.get('X-DrG-Recording')
    if value is None:return None
    try:
        if len(value)>2048:raise ValueError('Recording metadata too large')
        return Recording.model_validate(json.loads(value)).model_dump_json()
    except (ValueError,TypeError,ValidationError):fail(422,message('invalidChatRecording'))


def from_request(request,mime,fail):
    value=request_payload(request,fail)
    if value is None:return None
    try:return validate(json.loads(value),mime).model_dump_json()
    except (ValueError,TypeError,ValidationError):fail(422,message('invalidChatRecording'))


def file_row(c,fid,*,sender=None,version=None):
    query='SELECT f.*,r.payload AS recording_payload FROM files f LEFT JOIN chat_recordings r ON r.file_id=f.id WHERE f.id=?';params=[fid]
    if sender is not None:query+=' AND f.user_id=?';params.append(sender)
    if version is not None:query+=' AND f.version=?';params.append(version)
    return c.execute(query,tuple(params)).fetchone()


def public_payload(value):
    recording=json.loads(value)
    return {'kind':recording['kind'],'duration':recording['duration_ms']/1000,'waveform':recording.get('waveform',[])}


def validate_archive(c,invalid):
    from app.chat_storage import attachment_room
    for row in c.execute('SELECT r.file_id,r.payload,f.mime,f.kind FROM chat_recordings r LEFT JOIN files f ON f.id=r.file_id'):
        try:
            if row['kind']!='file' or not attachment_room(row['file_id']):raise ValueError('Invalid recording owner')
            validate(json.loads(row['payload']),row['mime'])
        except (ValueError,TypeError,ValidationError):invalid()
