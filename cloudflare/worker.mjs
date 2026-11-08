import {connectAsset} from './connect-page.mjs';
import {connectCookie,hasConnectCookie} from './connect-auth.mjs';
import {directMcp} from './direct-mcp.mjs';
import {bounded} from './direct-api.mjs';
export {DoctolibAccount} from './direct-state.mjs';
const enc=new TextEncoder();
const json=(status,error)=>Response.json({error},{status,headers:{'cache-control':'no-store','x-content-type-options':'nosniff'}});
async function authorize(request,env){
 if(env.DEPLOYMENT_OWNER!=='changepoint.eu'||!/^[a-f0-9]{64}$/.test(env.MCP_TOKEN_SHA256||''))return json(503,'SETUP_REQUIRED');
 if(new URL(request.url).origin!==env.PUBLIC_ORIGIN)return json(421,'WRONG_ORIGIN');
 const origin=request.headers.get('origin');if(origin&&origin!==env.PUBLIC_ORIGIN)return json(403,'ORIGIN_DENIED');
 const match=/^Bearer ([A-Za-z0-9_-]{40,128})$/.exec(request.headers.get('authorization')||'');if(!match)return json(401,'UNAUTHORIZED');
 const digest=await crypto.subtle.digest('SHA-256',enc.encode(match[1]));
 const expected=Uint8Array.from(env.MCP_TOKEN_SHA256.match(/../g),x=>parseInt(x,16));
 if(!crypto.subtle.timingSafeEqual(digest,expected))return json(401,'UNAUTHORIZED');
 return null;
}
async function directApiCheck(request, env) {
  // Fixed read-only search; no arbitrary URL, cookies, account records or credentials.
  if (request.method !== 'POST') return json(405,'METHOD_NOT_ALLOWED');
  if (env.DEPLOYMENT_OWNER !== 'changepoint.eu' || !/^[a-f0-9]{64}$/.test(env.MCP_TOKEN_SHA256 || '')) return json(503,'SETUP_REQUIRED');
  if (new URL(request.url).origin !== env.PUBLIC_ORIGIN) return json(421,'WRONG_ORIGIN');
  const origin = request.headers.get('origin');
  if (origin && origin !== env.PUBLIC_ORIGIN) return json(403,'ORIGIN_DENIED');
  const match = /^Bearer ([A-Za-z0-9_-]{40,128})$/.exec(request.headers.get('authorization') || '');
  if (!match) return json(401,'UNAUTHORIZED');
  const digest = await crypto.subtle.digest('SHA-256',enc.encode(match[1]));
  const expected = Uint8Array.from(env.MCP_TOKEN_SHA256.match(/../g),x=>parseInt(x,16));
  if (!crypto.subtle.timingSafeEqual(digest,expected)) return json(401,'UNAUTHORIZED');
  const endpoint='https://www.doctolib.de/patient-health-search/api/v1/hcp/search?page=0';
  const payload={keyword:'allgemeinmedizin',location:{place:{name:'Berlin',slug:'berlin',country:'de',type:'locality',viewport:{northeast:{lat:52.68,lng:13.76},southwest:{lat:52.35,lng:13.09}},gpsPoint:{lat:52.517,lng:13.3889}}},filters:{}};
  try {
    const response=await fetch(endpoint,{method:'POST',redirect:'manual',signal:AbortSignal.timeout(20000),headers:{'content-type':'application/json',accept:'application/json','user-agent':'changepoint-doctolib-mcp/0.2 (personal appointment assistant)'},body:JSON.stringify(payload)});
    const type=response.headers.get('content-type') || '';
    if (![200,206].includes(response.status)) {
      await response.body?.cancel();
      return Response.json({direct_api:false,authenticated:false,upstream_status:response.status,content_type:type,reason:response.status===403?'ACCESS_DENIED':response.status===429?'RATE_LIMITED':'UPSTREAM_REJECTED',retried:false},{headers:{'cache-control':'no-store'}});
    }
    // Only report schema recognition; never return profiles, patient data or raw bodies.
    const reader=response.body?.getReader();let total=0;const parts=[];
    if(reader) for(;;){const {value,done}=await reader.read();if(done)break;total+=value.byteLength;if(total>2*1024*1024){await reader.cancel();throw new Error('bounded response exceeded');}parts.push(value);}
    const data=new Uint8Array(total);let offset=0;for(const part of parts){data.set(part,offset);offset+=part.length;}
    let parsed;try{parsed=JSON.parse(new TextDecoder().decode(data));}catch{return Response.json({direct_api:false,authenticated:false,upstream_status:response.status,reason:'NON_JSON_RESPONSE'},{headers:{'cache-control':'no-store'}});}
    return Response.json({direct_api:Array.isArray(parsed.healthcareProviders),authenticated:false,upstream_status:response.status,search_schema_recognized:Array.isArray(parsed.healthcareProviders),retried:false},{headers:{'cache-control':'no-store'}});
  } catch {return json(502,'DIRECT_API_CHECK_FAILED');}
}
export default {
 async scheduled(event,env){if(env.ACCOUNT_STATE)await env.ACCOUNT_STATE.getByName('personal-de').tick();},
 async fetch(request,env){
  const url=new URL(request.url);
  if((url.pathname==='/connect'||url.pathname==='/connect.js')&&!url.search&&request.method==='GET')return connectAsset(url.pathname.endsWith('.js'));
  if(url.pathname.startsWith('/connect/')){
   if(env.DEPLOYMENT_OWNER!=='changepoint.eu'||url.origin!==env.PUBLIC_ORIGIN)return json(503,'SETUP_REQUIRED');
   if(url.search||!env.ACCOUNT_STATE)return json(400,'INVALID_CONNECT_REQUEST');
   const action=url.pathname.slice('/connect/'.length);
   if(!['unlock','status','start','finish','cancel'].includes(action))return json(404,'NOT_FOUND');
   if(request.method!==(action==='status'?'GET':'POST'))return json(405,'METHOD_NOT_ALLOWED');
   const origin=request.headers.get('origin');
   if(action!=='status'&&origin!==env.PUBLIC_ORIGIN||origin&&origin!==env.PUBLIC_ORIGIN)return json(403,'ORIGIN_DENIED');
   if(action==='unlock'){
    const denied=await authorize(request,env);if(denied)return denied;
    return Response.json({ok:true},{headers:{'set-cookie':await connectCookie(env),'cache-control':'no-store'}});
   }
   if(!await hasConnectCookie(request,env))return json(401,'UNAUTHORIZED');
   let id;
   if(action!=='status'){
    if(!request.headers.get('content-type')?.startsWith('application/json'))return json(415,'JSON_REQUIRED');
    try{const data=JSON.parse(await bounded(new Response(request.body),2048));if(Object.keys(data).some(k=>k!=='login_id')||data.login_id!==undefined&&!/^[a-f0-9-]{36}$/.test(data.login_id))return json(400,'INVALID_CONNECT_REQUEST');id=data.login_id;}catch{return json(400,'INVALID_CONNECT_REQUEST');}
   }
   try{const result=await env.ACCOUNT_STATE.getByName('personal-de').login(action,id);return Response.json(result,{status:result.ok===false?400:200,headers:{'cache-control':'no-store','referrer-policy':'no-referrer'}});}catch{return json(503,'LOGIN_FAILED');}
  }
  if(url.pathname==='/health'&&request.method==='GET')return Response.json({service:'doctolib-mcp',owner:'changepoint.eu',direct_api:true,storage_configured:!!env.ACCOUNT_STATE,session_secret_configured:!!env.DOCTOLIB_COOKIE,writes_enabled:env.ENABLE_WRITES==='true'},{headers:{'cache-control':'no-store'}});
  if(url.search||!['/mcp','/setup/check-api','/setup/session'].includes(url.pathname))return json(404,'NOT_FOUND');
  if(request.method!=='POST')return json(405,'POST_REQUIRED');
  const denied=await authorize(request,env);if(denied)return denied;
  if(url.pathname==='/setup/check-api')return directApiCheck(request,env);
  if(!env.ACCOUNT_STATE)return json(503,'STORAGE_SETUP_REQUIRED');
  if(url.pathname==='/setup/session'){try{const data=JSON.parse(await bounded(new Response(request.body),20000));if(Object.keys(data).length!==1||typeof data.cookie!=='string')return json(400,'SESSION_FORMAT_INVALID');const result=await env.ACCOUNT_STATE.getByName('personal-de').importSession(data.cookie);return Response.json(result,{status:result.ok?200:400,headers:{'cache-control':'no-store'}});}catch{return json(400,'SESSION_IMPORT_FAILED');}}
  try{return await directMcp(request,env);}catch{return json(400,'INVALID_MCP_REQUEST');}
 }
};
