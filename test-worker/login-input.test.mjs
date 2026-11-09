import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseInteraction,loginInteraction,loginFrame} from '../cloudflare/login-input.mjs';

function page({editable=true,auth=true}={}){
 const calls=[];const p={url:()=>auth?'https://www.doctolib.de/authn/patient/login':'https://www.doctolib.de/account/appointments',evaluate:async fn=>String(fn).includes('return e instanceof')?editable:editable?{type:'password',label:'Passwort'}:null,screenshot:async options=>{assert.equal(options.encoding,'base64');return 'RkFLRQ==';},waitForNetworkIdle:async()=>{},mouse:{click:async(x,y)=>calls.push(['click',x,y]),wheel:async v=>calls.push(['scroll',v.deltaY])},keyboard:{down:async key=>calls.push(['down',key]),up:async key=>calls.push(['up',key]),press:async key=>calls.push(['key',key]),sendCharacter:async text=>calls.push(['text',text])}};
 return {p,calls};
}
test('native mobile text replaces only the selected login input and is not returned',async()=>{
 const {p,calls}=page();const result=await loginInteraction(p,{kind:'text',text:'FAKE_PASSWORD_ONLY'});
 assert.deepEqual(calls,[['down','Control'],['key','a'],['up','Control'],['text','FAKE_PASSWORD_ONLY']]);
 assert.equal(result.field.type,'password');assert.ok(!JSON.stringify(result).includes('FAKE_PASSWORD_ONLY'));assert.equal(result.image,'RkFLRQ==');
});
test('input requires a selected editable field and stays on the authentication page',async()=>{
 const x=page({editable:false});await assert.rejects(()=>loginInteraction(x.p,{kind:'text',text:'FAKE'}),/LOGIN_SELECT_INPUT_FIRST/);assert.equal(x.calls.length,0);
 const y=page({auth:false});await assert.rejects(()=>loginInteraction(y.p,{kind:'key',key:'Enter'}),/LOGIN_READY_TO_VERIFY/);assert.equal(y.calls.length,0);assert.deepEqual(await loginFrame(y.p),{ready_to_verify:true,image:null,field:null,width:390,height:780});
});
test('mobile interactions reject URLs, scripts, unsupported keys and out-of-range coordinates',()=>{
 for(const value of [{kind:'navigate',url:'https://evil.test'},{kind:'key',key:'F12'},{kind:'click',x:390,y:4},{kind:'click',x:-1,y:4},{kind:'scroll',delta:10000},{kind:'text',text:'x',url:'https://evil.test'},{kind:'text',text:'x\n'},{kind:'text',text:'x'.repeat(2049)}])assert.throws(()=>parseInteraction(value),/LOGIN_INPUT_INVALID/);
 assert.deepEqual(parseInteraction({kind:'click',x:20,y:40}),{kind:'click',x:20,y:40});
});
