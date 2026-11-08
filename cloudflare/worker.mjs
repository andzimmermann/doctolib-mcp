const MAX_BODY = 524288;
const enc = new TextEncoder();
const json = (status, error) => Response.json({error}, {status, headers:{'cache-control':'no-store','x-content-type-options':'nosniff'}});
function config(env) {
  if (env.DEPLOYMENT_OWNER !== 'changepoint.eu' || !/^[a-f0-9]{64}$/.test(env.MCP_TOKEN_SHA256 || '') || !env.BACKEND_TOKEN || env.BACKEND_TOKEN.length < 40) return null;
  try {
    const backend = new URL(env.BACKEND_URL);
    const publicOrigin = new URL(env.PUBLIC_ORIGIN);
    if (backend.protocol !== 'https:' || backend.username || backend.password || backend.pathname !== '/mcp' || backend.search || backend.hash) return null;
    if (publicOrigin.protocol !== 'https:' || publicOrigin.origin !== env.PUBLIC_ORIGIN || backend.origin === publicOrigin.origin) return null;
    if (backend.hostname !== env.BACKEND_HOST || backend.hostname.endsWith('.local') || !backend.hostname.includes('.') || /^[\d.:]+$/.test(backend.hostname)) return null;
    return {backend, publicOrigin};
  } catch {return null;}
}
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/health' && request.method === 'GET') return Response.json({service:'doctolib-mcp-gateway',owner:'changepoint.eu',configured:!!config(env)}, {status:config(env)?200:503,headers:{'cache-control':'no-store'}});
    if (url.pathname !== '/mcp' || url.search) return json(404,'NOT_FOUND');
    if (!['GET','POST','DELETE'].includes(request.method)) return json(405,'METHOD_NOT_ALLOWED');
    const cfg = config(env);
    if (!cfg) return json(503,'SETUP_REQUIRED');
    if (url.origin !== cfg.publicOrigin.origin) return json(421,'WRONG_ORIGIN');
    const auth = /^Bearer ([A-Za-z0-9_-]{40,128})$/.exec(request.headers.get('authorization') || '');
    if (!auth) return json(401,'UNAUTHORIZED');
    const digest = await crypto.subtle.digest('SHA-256', enc.encode(auth[1]));
    const expected = Uint8Array.from(env.MCP_TOKEN_SHA256.match(/../g), x=>parseInt(x,16));
    if (!crypto.subtle.timingSafeEqual(digest, expected)) return json(401,'UNAUTHORIZED');
    const origin = request.headers.get('origin');
    if (origin && origin !== cfg.publicOrigin.origin) return json(403,'ORIGIN_DENIED');
    const headers = new Headers({authorization:'Bearer '+env.BACKEND_TOKEN,accept:request.headers.get('accept') || 'application/json, text/event-stream'});
    for (const name of ['content-type','mcp-protocol-version','mcp-session-id','last-event-id']) {
      const value = request.headers.get(name); if(value) headers.set(name,value);
    }
    let body;
    if (request.body) {
      const size = Number(request.headers.get('content-length'));
      if (size > MAX_BODY) return json(413,'BODY_TOO_LARGE');
      const reader = request.body.getReader(); const chunks=[]; let total=0;
      for (;;) {const {value,done}=await reader.read();if(done)break;total+=value.byteLength;if(total>MAX_BODY){await reader.cancel();return json(413,'BODY_TOO_LARGE');}chunks.push(value);}
      body = new Uint8Array(total); let offset=0;for(const c of chunks){body.set(c,offset);offset+=c.length;}
    }
    try {
      const upstream = await fetch(cfg.backend.href,{method:request.method,headers,body,redirect:'manual',signal:AbortSignal.timeout(90000)});
      // Never follow redirects or repeat a failed request: it could already have booked.
      if(upstream.status>=300&&upstream.status<400){await upstream.body?.cancel();return json(502,'BACKEND_REDIRECT_REJECTED_OUTCOME_UNKNOWN');}
      if(upstream.status>=500){await upstream.body?.cancel();return json(502,'BACKEND_FAILED_OUTCOME_UNKNOWN');}
      const responseHeaders = new Headers({'cache-control':'no-store','x-content-type-options':'nosniff'});
      for(const name of ['content-type','mcp-session-id','mcp-protocol-version','retry-after']){
        const value=upstream.headers.get(name);if(value)responseHeaders.set(name,value);
      }
      return new Response(upstream.body,{status:upstream.status,headers:responseHeaders});
    } catch {return json(502,'BACKEND_UNREACHABLE_OUTCOME_UNKNOWN');}
  }
};
