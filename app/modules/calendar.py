from app.http import Router,Request
from app.i18n import message

def create_calendar_router(service,ready,fail):
    router=Router()

    @router.get('/api/calendar/holidays')
    async def holidays(request:Request,year:int):
        ready(request)
        if not 1900<=year<=2200:fail(422,message('calendarYearInvalid'))
        return await service.get(year)

    return router
