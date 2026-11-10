"""Real transition history drives order across edits, re-entry and imports."""
import pytest
from app import tracker
from tests.test_storage_encryption import databases,seed


@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_board_timestamps_ignore_edits_and_read_all_history(databases,engine):
    backend=databases(engine);seed(backend);uid='a'*32
    with backend.connection() as c:
        project=tracker.create_project(c,tracker.Project(name='Ordering',key='ORDER'),uid,100)
        a=tracker.create_issue(c,project['id'],tracker.Issue(title='Older'),uid,101)
        b=tracker.create_issue(c,project['id'],tracker.Issue(title='Newer',status='work'),uid,102)
        def update(item,stamp,**changes):
            return tracker.update_issue(c,item['id'],tracker.IssueUpdate(version=item['version'],**changes),uid,stamp)
        a=update(a,110,status='work')
        assert a['status_entered_at']==110 and b['status_entered_at']==102
        for index in range(60):a=update(a,120+index,title='Edited '+str(index))
        assert a['updated']==179 and a['status_entered_at']==110
        a=update(a,180,status='review');assert a['status_entered_at']==180
        a=update(a,190,status='work');assert a['status_entered_at']==190
        a=update(a,200,status='done');assert a['status_entered_at']==200
        a=update(a,210,title='Completed edit');assert a['status_entered_at']==200
        a=update(a,220,status='todo');assert a['status_entered_at']==101
        a=update(a,230,status='done');assert a['status_entered_at']==230
        # Historical imports with no transitions retain their real dates.
        old=tracker.create_issue(c,project['id'],tracker.Issue(title='Legacy',status='review'),uid,300,created=50,imported=True)
        assert old['status_entered_at']==50
        page=tracker.issue_page(c,project['id'],uid,400,limit=2)
        assert page['more'] and {r['id']:r['status_entered_at'] for r in page['items']}=={a['id']:230,b['id']:102}
        rest=tracker.issue_page(c,project['id'],uid,400,after=page['cursor'],limit=2)
        assert rest['items'][0]['status_entered_at']==50
