const enc=new TextEncoder(),name='__Host-doctolib-connect';
const bytes=hex=>Uint8Array.from(hex.match(/../g)||[],n=>parseInt(n,16));
const hex=bytes=>Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
async function key(env){if(!/^[a-f0-9]{64}$/.test(env.SESSION_KEY||''))throw Error('SETUP_REQUIRED');return crypto.subtle.importKey('raw',bytes(env.SESSION_KEY),{name:'HMAC',hash:'SHA-256'},false,['sign','verify']);}
const message=(value,env)=>enc.encode('doctolib-connect-v1:'+value+':'+env.MCP_TOKEN_SHA256);
export async function connectCookie(env){const value=(Date.now()+30*86400000)+'.'+crypto.randomUUID();const signature=hex(new Uint8Array(await crypto.subtle.sign('HMAC',await key(env),message(value,env))));return name+'='+value+'.'+signature+'; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=2592000';}
export async function hasConnectCookie(request,env){
 const value=(request.headers.get('cookie')||'').split(';').map(x=>x.trim()).find(x=>x.startsWith(name+'='))?.slice(name.length+1);
 if(!value||value.length>200)return false;
 const match=/^(\d{13})\.([a-f0-9-]{36})\.([a-f0-9]{64})$/.exec(value);
 if(!match||Number(match[1])<=Date.now()||Number(match[1])>Date.now()+30*86400000)return false;
 try{return await crypto.subtle.verify('HMAC',await key(env),bytes(match[3]),message(match[1]+'.'+match[2],env));}catch{return false;}
}
