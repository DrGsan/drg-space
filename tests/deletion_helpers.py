"""Advance only isolated retention state; never change the wall clock or auth."""
def finish_account(uid):
    from app import main as m
    from app.account_lifecycle import pending,purge
    with m.db() as c:purge(c,pending(c,uid)['delete_at'])
    m.files_router.cleanup()
