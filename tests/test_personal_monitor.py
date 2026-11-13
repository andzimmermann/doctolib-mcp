from datetime import datetime, timezone
from contextlib import nullcontext

import httpx
import pytest

from doctolib_mcp.client import DoctolibClient, DoctolibError
from doctolib_mcp.monitor import MonitorStore, validate
from doctolib_mcp.booking import DoctolibBookingClient

NOW = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
SLOT = '2026-11-10T10:00:00+01:00'

@pytest.fixture
def store(tmp_path):
    s = MonitorStore(tmp_path / 'jobs.sqlite3')
    yield s
    s.close()

@pytest.fixture
def rule():
    return dict(country='de', profile_slug='praxis-beispiel', profile_id=1,
                visit_motive_id=2, agenda_id=3, practice_id=4, patient_id='child-uuid',
                date_from='2026-11-09', date_to='2026-11-30', time_from='09:00',
                time_to='17:00', weekdays=[0,1,2,3,4], timezone='Europe/Berlin',
                interval_seconds=300, auto_book=True, new_patient=False)

class Read:
    def __init__(self, slots=None, bad_profile=False):
        self.slots = slots if slots is not None else [SLOT]
        self.bad_profile = bad_profile
    def practitioner(self, slug):
        return {'profile_id': 99 if self.bad_profile else 1,
                'agendas': [{'id':3,'practice_id':4,'visit_motive_ids':[2]}],
                'visit_motives':[{'id':2,'allow_new_patients':True}]}
    def availabilities(self,*a,**kw):
        return {'days_with_slots':[{'slots':[{'start_date':s} for s in self.slots]}]}

def run(store, read=None, book=None, people=None):
    return store.tick(now=NOW, read_client=lambda country:nullcontext(read or Read()),
        book=book or (lambda country,args:{'booked':True}),
        patients=lambda country:people if people is not None else [{'uuid':'child-uuid','kind':'dependent'}])

def test_exact_patient_selected_before_any_booking(store,rule):
    job=store.create(rule);calls=[]
    run(store,book=lambda country,args:calls.append(args) or {'booked':True})
    assert calls[0]['patient_id']=='child-uuid'
    assert calls[0]['practice_ids']==[4] and calls[0]['visit_motive_ids']==[2]
    assert store.list()[0]['state']=='booked'
    run(store,book=lambda *a:pytest.fail('duplicate booking'))
    assert len(calls)==1

def test_patient_uuid_does_not_fall_back_to_account_holder(store,rule):
    store.create(rule)
    run(store,people=[{'uuid':'parent','kind':'main'}],book=lambda *a:pytest.fail('wrong person'))
    assert store.list()[0]['state']=='needs_review'

def test_confirmed_patient_id_wins_over_ambiguous_name():
    people=[{'uuid':'one','first_name':'Alex'},{'uuid':'two','first_name':'Alex'}]
    assert DoctolibBookingClient._pick_patient(people,'Alex','two')['uuid']=='two'
    with pytest.raises(Exception):DoctolibBookingClient._pick_patient(people,None,'missing')

def test_only_instructions_matching_date_weekday_time_are_booked(store,rule):
    store.create(rule)
    run(store,Read(['2026-11-08T10:00:00+01:00','2026-12-01T10:00:00+01:00',
                    '2026-11-14T10:00:00+01:00','2026-11-10T18:00:00+01:00']),
        book=lambda *a:pytest.fail('outside instruction'))
    assert store.list()[0]['state']=='active'
    assert store.list()[0]['next_check']==NOW.timestamp()+300

def test_notify_only_never_books(store,rule):
    rule['auto_book']=False;store.create(rule)
    run(store,book=lambda *a:pytest.fail('no standing authorization'))
    assert store.list()[0]['state']=='found'

def test_expired_rule_makes_no_requests(store,rule):
    rule['date_from']='2026-10-01';rule['date_to']='2026-10-30';store.create(rule)
    store.tick(now=NOW,patients=lambda *a:pytest.fail('expired'))
    assert store.list()[0]['state']=='expired'

def test_changed_practice_is_paused(store,rule):
    store.create(rule);run(store,Read(bad_profile=True),book=lambda *a:pytest.fail('wrong practice'))
    assert store.list()[0]['state']=='needs_review'

def test_ambiguous_booking_does_not_retry_after_restart(store,rule):
    store.create(rule);run(store,book=lambda *a:{'booked':None,'outcome':'unknown'})
    assert store.list()[0]['state']=='needs_review'
    second=MonitorStore(store.path)
    try:run(second,book=lambda *a:pytest.fail('unknown outcome repeated'))
    finally:second.close()

def test_crash_during_booking_keeps_persistent_reservation(store,rule):
    store.create(rule)
    def crash(*a):raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):run(store,book=crash)
    assert store.list()[0]['state']=='booking'
    run(store,book=lambda *a:pytest.fail('interrupted booking repeated'))

def test_duplicate_jobs_cannot_book_same_target_and_patient(store,rule):
    store.create(rule);store.create(rule);calls=[]
    run(store,book=lambda *a:calls.append(a) or {'booked':True})
    assert len(calls)==1
    assert sorted(j['state'] for j in store.list())==['booked','needs_review']

def test_stop_prevents_future_checks(store,rule):
    j=store.create(rule);assert store.stop(j['id'])['stopped']
    store.tick(now=NOW,patients=lambda *a:pytest.fail('stopped'))

@pytest.mark.parametrize('change',[{'interval_seconds':1},{'patient_id':''},{'profile_slug':'https://evil.test'},
                                  {'weekdays':[7]},{'auto_book':'true'},{'practice_id':True},
                                  {'date_to':'2027-11-01'},{'time_to':'08:00'}])
def test_reject_invalid_or_unbounded_rules(rule,change):
    with pytest.raises((ValueError,TypeError)):validate({**rule,**change})

def test_doctolib_cookie_is_never_sent_to_geocoder():
    seen=[]
    with DoctolibClient(cookie='session=FAKE_TEST_ONLY') as c:
        jar=c._http.cookies;c._http.close()
        def handler(r):
            seen.append((r.url.host,r.headers.get('cookie')))
            return httpx.Response(200,json=[{'boundingbox':['1','2','3','4'],'lat':'1','lon':'3'}])
        c._http=httpx.Client(transport=httpx.MockTransport(handler),cookies=jar)
        c.resolve_place('Berlin')
        c._http.get(c.base+'/test')
    assert seen==[('nominatim.openstreetmap.org',None),('www.doctolib.de','session=FAKE_TEST_ONLY')]

def test_all_availability_failures_raise_instead_of_appearing_empty(monkeypatch):
    with DoctolibClient() as c:
        monkeypatch.setattr(c,'search',lambda *a,**k:{'total':1,'providers':[{'visit_motive_id':1,'agenda_ids':[2],'practice_id':3}]})
        def denied(*a,**k):raise DoctolibError('HTTP 403')
        monkeypatch.setattr(c,'availabilities',denied)
        with pytest.raises(DoctolibError,match='unknown'):c.earliest('test',city='Berlin')

def test_missing_account_session_does_not_silently_search_anonymously(monkeypatch):
    from doctolib_mcp import session,mcp_server
    monkeypatch.setenv('DOCTOLIB_AUTO_LOGIN','0')
    def missing(*a,**k):raise session.NotLoggedIn('login required')
    monkeypatch.setattr(session,'cookie_header',missing)
    with pytest.raises(session.NotLoggedIn):mcp_server._read_client('de')

def test_earliest_sorts_real_dictionary_slots_and_marks_partial_results(monkeypatch):
    with DoctolibClient() as c:
        providers=[{'visit_motive_id':i,'agenda_ids':[2],'practice_id':3} for i in (1,2,3)]
        monkeypatch.setattr(c,'search',lambda *a,**k:{'total':3,'providers':providers})
        def av(motive,*a,**k):
            if motive==3:raise DoctolibError('unavailable')
            return {'first_slot':{'start_date':f'2026-11-{12-motive:02d}T10:00:00+01:00'},'total':1,'days_with_slots':[],'reason':None}
        monkeypatch.setattr(c,'availabilities',av)
        out=c.earliest('test',city='Berlin')
        assert [p['visit_motive_id'] for p in out['results']]==[2,1]
        assert out['failed_checks']==1 and out['partial_results'] is True

@pytest.mark.parametrize('appointment',[{'profile_id':99},{'new_patient':True}])
def test_prefilled_form_cannot_change_instructed_practice_or_patient_status(appointment):
    with pytest.raises(Exception):DoctolibBookingClient._build_confirm_payload({'appointment':appointment},1,False)

def test_two_scheduler_processes_cannot_dispatch_the_same_job_twice(store,rule):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock
    store.create(rule)
    calls=[];lock=Lock()
    def book(*args):
        with lock:calls.append(args)
        return {'booked':True}
    def worker():
        s=MonitorStore(store.path)
        try:return run(s,book=book)
        finally:s.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _:worker(),range(2)))
    assert len(calls)==1
    assert store.list()[0]['state']=='booked'
