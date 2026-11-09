import {ApiError} from './direct-api.mjs';
export function parseInteraction(value){
 if(!value||typeof value!=='object'||Array.isArray(value))throw new ApiError('LOGIN_INPUT_INVALID');
 const keys=Object.keys(value),only=allowed=>keys.every(k=>allowed.includes(k));
 if(value.kind==='click'&&only(['kind','x','y'])&&Number.isInteger(value.x)&&Number.isInteger(value.y)&&value.x>=0&&value.x<390&&value.y>=0&&value.y<780)return value;
 if(value.kind==='text'&&only(['kind','text'])&&typeof value.text==='string'&&value.text.length>0&&value.text.length<=2048&&!/[\r\n\0]/.test(value.text))return value;
 if(value.kind==='key'&&only(['kind','key'])&&['Enter','Tab','Backspace'].includes(value.key))return value;
 if(value.kind==='scroll'&&only(['kind','delta'])&&Number.isInteger(value.delta)&&Math.abs(value.delta)<=650)return value;
 throw new ApiError('LOGIN_INPUT_INVALID');
}
function authPage(page){const u=new URL(page.url());return u.protocol==='https:'&&['doctolib.de','doctolib.com'].some(h=>u.hostname===h||u.hostname.endsWith('.'+h))&&/^\/(?:authn\/|sessions(?:\/|$)|login(?:\/|$)|account\/sign_in)/.test(u.pathname);}
export async function loginFrame(page){
 if(!authPage(page))return {ready_to_verify:true,image:null,field:null,width:390,height:780};
 const field=await page.evaluate(()=>{
  const input=document.activeElement;
  if(!(input instanceof HTMLInputElement)||input.disabled||input.readOnly||!['text','email','password','tel','number'].includes(input.type))return null;
  return {type:input.type,label:(input.labels?.[0]?.innerText||input.getAttribute('aria-label')||input.placeholder||'Eingabe').slice(0,160)};
 });
 return {image:await page.screenshot({type:'jpeg',quality:65,encoding:'base64',fullPage:false}),field,width:390,height:780};
}
export async function loginInteraction(page,raw){
 const input=parseInteraction(raw);
 if(!authPage(page))throw new ApiError('LOGIN_READY_TO_VERIFY');
 if(input.kind==='click')await page.mouse.click(input.x,input.y);
 if(input.kind==='scroll')await page.mouse.wheel({deltaY:input.delta});
 if(input.kind==='key')await page.keyboard.press(input.key);
 if(input.kind==='text'){
  const editable=await page.evaluate(()=>{const e=document.activeElement;return e instanceof HTMLInputElement&&!e.disabled&&!e.readOnly&&['text','email','password','tel','number'].includes(e.type);});
  if(!editable)throw new ApiError('LOGIN_SELECT_INPUT_FIRST');
  await page.keyboard.down('Control');try{await page.keyboard.press('a');}finally{await page.keyboard.up('Control');}
  // sendCharacter is the installed Cloudflare SDK's supported Input.insertText bridge.
  await page.keyboard.sendCharacter(input.text);
 }
 await page.waitForNetworkIdle({idleTime:150,timeout:1500}).catch(()=>{});
 return loginFrame(page);
}
