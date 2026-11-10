"""One password policy for account creation and password changes."""
from app.i18n import message as localized_message
from typing import Annotated
from pydantic import AfterValidator,Field

def validate_password(value):
    if not (any(c.islower() for c in value) and any(c.isupper() for c in value) and any(c.isdecimal() for c in value)):
        raise ValueError(localized_message('thePasswordMustContainBothUppercaseAndLowercase'))
    return value

NewPassword=Annotated[str,Field(min_length=12,max_length=256),AfterValidator(validate_password)]

PENDING_KEYS=('_pending_totp','_pending_until','_pending_codes','_pending_counter','_pending_session')
def clear_enrollment(settings):
    for key in PENDING_KEYS:settings.pop(key,None)
