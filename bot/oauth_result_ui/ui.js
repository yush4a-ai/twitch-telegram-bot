const title=document.querySelector('#result-title'),description=document.querySelector('#result-description'),account=document.querySelector('#result-account'),retry=document.querySelector('#result-retry');
const messages={pending:['Проверяем подключение…','Подтверждаем ваш аккаунт Twitch. Не закрывайте страницу.'],verifying:['Проверяем подключение…','Подтверждаем ваш аккаунт Twitch. Не закрывайте страницу.'],connected:['Twitch подключён','Вернитесь в приложение, чтобы выбрать Telegram-канал.'],cancelled:['Подключение отменено','Аккаунт не подключён. Можно начать заново в приложении.'],expired:['Ссылка устарела','Откройте приложение и начните подключение заново.'],failed:['Не удалось подключить Twitch','Аккаунт не подключён. Попробуйте ещё раз в приложении.'],conflict:['Аккаунт уже связан','Проверьте привязку в боте, прежде чем подключать его снова.']};
let disposed=false,busy=false,timer=null,controller=null;
function renderAccount(login){
  account.replaceChildren();
  if(typeof login!=='string'||!login)return;
  const badge=document.createElement('span');
  badge.className='account-value';
  badge.textContent=login;
  account.append(badge);
}
function render(result){const copy=messages[result.status];if(!copy)throw new Error('Unknown result status');title.textContent=copy[0];description.textContent=copy[1];renderAccount(result.status==='connected'?result.twitch_login:'');document.body.dataset.resultStatus=result.status;retry.hidden=!['pending','verifying'].includes(result.status);}
async function check(){
  if(disposed||busy||document.hidden)return;busy=true;retry.disabled=true;clearTimeout(timer);controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),5000);
  try{const response=await fetch('/twitch/result/status',{credentials:'same-origin',cache:'no-store',signal:controller.signal});if(!response.ok)throw new Error('Result unavailable');const result=await response.json();if(!disposed)render(result);}
  catch{if(!disposed){description.textContent='Не удалось проверить подключение. Повторите, когда связь восстановится.';retry.hidden=false;}}
  finally{clearTimeout(timeout);busy=false;if(!disposed){retry.disabled=false;if(!retry.hidden)timer=setTimeout(check,2000);}}
}
retry.addEventListener('click',check);document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!retry.hidden)void check();});window.addEventListener('pagehide',()=>{disposed=true;clearTimeout(timer);controller?.abort();},{once:true});
retry.hidden=!['pending','verifying'].includes(document.body.dataset.resultStatus);if(!retry.hidden)void check();
