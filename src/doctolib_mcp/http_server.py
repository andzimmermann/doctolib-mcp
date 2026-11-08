"""Authenticated, stateless HTTP entry point for the ChangePoint Worker.

Run behind an HTTPS reverse proxy; bind loopback by default. No login UI is exposed.
Use the existing interactive CLI login on the same host/profile. DOCTOLIB_BACKEND_TOKEN
is independent from the client-facing MCP token and must never be logged.
"""
from __future__ import annotations
import hashlib
import hmac
import os

from mcp.server.transport_security import TransportSecuritySettings
from .mcp_server import mcp

MAX_BODY=524288

class BearerAuth:
    def __init__(self, app, token: str):
        if len(token)<40:
            raise ValueError('A strong DOCTOLIB_BACKEND_TOKEN is required')
        self.app=app
        self.digest=hashlib.sha256(token.encode()).digest()

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':
            return await self.app(scope,receive,send)
        headers=dict(scope.get('headers',[]))
        auth=headers.get(b'authorization',b'')
        given=auth[7:] if auth.startswith(b'Bearer ') else b''
        if not hmac.compare_digest(hashlib.sha256(given).digest(),self.digest):
            return await self.reject(send,401,b'Unauthorized')
        if scope.get('path')!='/mcp' or scope.get('query_string'):
            return await self.reject(send,404,b'Not found')
        if scope.get('method') not in ('GET','POST','DELETE'):
            return await self.reject(send,405,b'Method not allowed')
        if headers.get(b'origin'):
            return await self.reject(send,403,b'Browser origin not allowed at backend')
        # Bound authenticated bodies before handing them to the MCP SDK.
        chunks=[];total=0
        while True:
            message=await receive()
            if message['type']=='http.disconnect':return
            total+=len(message.get('body',b''))
            if total>MAX_BODY:return await self.reject(send,413,b'Too large')
            chunks.append(message.get('body',b''))
            if not message.get('more_body',False):break
        first=True
        async def bounded_receive():
            nonlocal first
            if first:
                first=False
                return {'type':'http.request','body':b''.join(chunks),'more_body':False}
            return await receive()
        # The SDK never receives the gateway credential, only the transport metadata.
        clean={**scope,'headers':[(k,v) for k,v in scope.get('headers',[]) if k.lower() not in (b'authorization',b'cookie')]}
        async def no_cache(message):
            if message['type']=='http.response.start':
                message={**message,'headers':[*message.get('headers',[]),(b'cache-control',b'no-store')]}
            await send(message)
        return await self.app(clean,bounded_receive,no_cache)

    @staticmethod
    async def reject(send,status,body):
        await send({'type':'http.response.start','status':status,'headers':[(b'content-type',b'text/plain'),(b'cache-control',b'no-store')]})
        await send({'type':'http.response.body','body':body})


def create_app():
    # HTTP requests never open an unattended login prompt.
    os.environ['DOCTOLIB_AUTO_LOGIN']='0'
    token=os.environ.get('DOCTOLIB_BACKEND_TOKEN','')
    allowed=[s.strip() for s in os.environ.get('DOCTOLIB_BACKEND_HOSTS','localhost,127.0.0.1').split(',') if s.strip()]
    if any('*' in s or '/' in s for s in allowed):raise ValueError('Exact backend hosts are required')
    app=mcp.streamable_http_app(json_response=True,stateless_http=True,max_request_body_size=MAX_BODY,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,allowed_hosts=allowed,allowed_origins=[]))
    return BearerAuth(app,token)


def main():
    import uvicorn
    uvicorn.run(create_app(),host='127.0.0.1',port=8765,access_log=False,log_level='warning')

if __name__=='__main__':main()
