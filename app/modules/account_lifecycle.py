from app.i18n import message as localized_message
from pydantic import BaseModel,ConfigDict,Field
from app.http import Router,Request
from app.account_lifecycle import schedule,cancel

class DeleteAccount(BaseModel):
    model_config=ConfigDict(extra='forbid')
    password:str=Field(min_length=1,max_length=256)
    confirmation:str=Field(min_length=3,max_length=40)

def create_account_lifecycle_router(db,ready,check_password,now,fail,gate,protection):
    router=Router()
    @router.post('/api/account/deletion')
    async def delete_account(data:DeleteAccount,request:Request):
        s=ready(request);protection.rate(request,'delete-account',10,subject=s['user_id'])
        async with gate.exclusive():
            s=ready(request)
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                user=c.execute('SELECT password,login FROM users WHERE id=?',(s['user_id'],)).fetchone()
                if not check_password(data.password,user['password']):fail(403,localized_message('incorrectPassword'))
                if data.confirmation!=user['login']:fail(422,localized_message('toDeleteEnterYourLogin'))
                return {'ok':True,'deletion':schedule(c,s['user_id'],s['user_id'],now(),fail)}
    @router.post('/api/account/deletion/restore')
    def restore_account(request:Request):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');cancel(c,s['user_id'],now(),fail)
        return {'ok':True}
    return router
