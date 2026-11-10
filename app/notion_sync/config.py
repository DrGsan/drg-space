"""No credentials means no worker, outgoing calls or startup failure."""
from dataclasses import dataclass
import os
from urllib.parse import urlsplit

@dataclass(frozen=True)
class NotionConfig:
    enabled:bool=False
    client_id:str=''
    client_secret:str=''
    redirect_uri:str=''
    read_webhook_secret:str=''
    reconcile_seconds:int=900

    @classmethod
    def from_env(cls,public_url):
        return cls(enabled=os.getenv('NOTION_SYNC_ENABLED','false').lower()=='true',
                   client_id=os.getenv('NOTION_OAUTH_CLIENT_ID','').strip(),client_secret=os.getenv('NOTION_OAUTH_CLIENT_SECRET','').strip(),
                   redirect_uri=os.getenv('NOTION_OAUTH_REDIRECT_URI','').strip() or public_url.rstrip('/')+'/api/notion-sync/oauth/callback',
                   read_webhook_secret=os.getenv('NOTION_WEBHOOK_READ_SECRET','').strip())

    @property
    def configured(self):
        try:p=urlsplit(self.redirect_uri)
        except ValueError:return False
        return bool(self.enabled and self.client_id and self.client_secret and p.scheme=='https' and p.hostname and not p.username and not p.password and not p.query and not p.fragment)

    @property
    def write_configured(self):return self.configured

    def credentials(self,kind):
        if kind in ('read','write') and self.configured:return self.client_id,self.client_secret
        raise ValueError('notionNotConfigured')
