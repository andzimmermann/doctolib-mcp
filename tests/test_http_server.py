import asyncio
import pytest
from starlette.testclient import TestClient
from doctolib_mcp.http_server import BearerAuth,create_app

TOKEN='FAKE_BACKEND_TOKEN_FOR_OFFLINE_TESTS_ONLY_1234567890'

def invoke(headers=None,body=b'{}',path='/mcp'):
    sent=[];called=[]
    async def app(scope,receive,send):
        called.append(scope)
        message=await receive()
        assert message['body']==body
        await send({'type':'http.response.start','status':200,'headers':[]})
        await send({'type':'http.response.body','body':b'ok'})
    async def receive():return {'type':'http.request','body':body,'more_body':False}
    async def send(message):sent.append(message)
    scope={'type':'http','path':path,'query_string':b'','method':'POST','headers':headers or []}
    asyncio.run(BearerAuth(app,TOKEN)(scope,receive,send))
    return sent,called

def test_backend_requires_its_own_token_before_reading_body():
    sent,called=invoke();assert sent[0]['status']==401 and not called

def test_backend_token_is_removed_before_sdk_and_cookie_is_never_forwarded():
    sent,called=invoke([(b'authorization',('Bearer '+TOKEN).encode()),(b'cookie',b'forbidden')])
    assert sent[0]['status']==200
    assert called[0]['headers']==[]

def test_backend_rejects_large_bodies_and_browser_origins():
    auth=[(b'authorization',('Bearer '+TOKEN).encode())]
    assert invoke(auth,b'x'*524289)[0][0]['status']==413
    assert invoke(auth+[(b'origin',b'https://evil.example.test')])[0][0]['status']==403

def test_backend_refuses_missing_setup(monkeypatch):
    monkeypatch.delenv('DOCTOLIB_BACKEND_TOKEN',raising=False)
    with pytest.raises(ValueError):create_app()

def test_actual_http_mcp_initialize_and_tools_list(monkeypatch):
    monkeypatch.setenv('DOCTOLIB_BACKEND_TOKEN',TOKEN)
    monkeypatch.setenv('DOCTOLIB_BACKEND_HOSTS','localhost')
    with TestClient(create_app(),base_url='http://localhost') as client:
        h={'authorization':'Bearer '+TOKEN,'accept':'application/json, text/event-stream'}
        init=client.post('/mcp',headers=h,json={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'offline-http-test','version':'1'}}})
        assert init.status_code==200,init.text
        listed=client.post('/mcp',headers=h,json={'jsonrpc':'2.0','id':2,'method':'tools/list'})
        assert listed.status_code==200,listed.text
        assert len(listed.json()['result']['tools'])==15
        assert 'cookie' not in str([t['inputSchema'].get('properties',{}) for t in listed.json()['result']['tools']])
