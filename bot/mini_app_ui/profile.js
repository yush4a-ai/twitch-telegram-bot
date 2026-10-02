import {element,action,dialog,navigationRow} from './components.js';

const themes=[['light','Светлая'],['dark','Тёмная'],['telegram','Как в Telegram']];
const date=value=>value?new Date(value*1000).toLocaleDateString('ru-RU',{dateStyle:'medium'}):'';

export function createProfileFeature(api,getRouter,theme) {
  let state=null,loading=false,error='',disposed=false;
  async function refresh(){
    if(loading||disposed)return;loading=true;
    try{state=await api.post('/app/api/subscription/state');error='';}
    catch{error='Не удалось обновить подписку. Попробуйте ещё раз.';}
    finally{loading=false;if(!disposed)getRouter().refresh();}
  }
  function themePicker(event){
    dialog('Тема приложения',content=>{
      const controls=[];
      for(const [id,label] of themes){
        const button=action(label,()=>{theme.setChoice(id);for(const [key,node] of controls)node.setAttribute('aria-pressed',String(key===id));},true);
        button.className='theme-choice';button.setAttribute('aria-pressed',String(theme.getChoice()===id));controls.push([id,button]);content.append(button);
      }
    },{origin:event.currentTarget});
  }
  return {
    refresh,
    render(target){
      target.replaceChildren();target.dataset.profileState=state?'ready':error?'error':'loading';if(!state&&!loading&&!error)void refresh();target.append(element('h1','','Профиль'));
      const identity=api.user,account=element('section','account-row');
      const avatar=element('span','avatar',(identity?.display_name||String(identity?.id||'')).slice(0,1).toUpperCase());avatar.setAttribute('aria-hidden','true');
      const copy=element('div','row-copy');copy.append(element('strong','row-title',identity?.display_name||`Telegram ID ${identity?.id}`));
      copy.append(element('small','muted',identity?.username?`@${identity.username}`:`Telegram ID ${identity?.id}`));account.append(avatar,copy);target.append(account);
      if(error)target.append(element('p','notice error',error),action('Обновить подписку',refresh,true));
      const active=state?.viewer.active||state?.streamer.active,product=state?.streamer.active?'Streamer Plus':state?.viewer.active?'Viewer Plus':'Free';
      const group=element('section','navigation-group');
      group.append(navigationRow(active?'Моя подписка':'Возможности Plus',active?`${product} · до ${date(state?.streamer.active?state.streamer.expires_at:state.viewer.expires_at)}`:'Больше стримеров, видео и точные уведомления','plus',()=>getRouter().openDetail('subscription'),'subscription'));
      group.append(navigationRow('Уведомления','Тихие часы и настройки зрителя','notification',()=>getRouter().openDetail('viewer-settings'),'viewer-settings'));
      group.append(navigationRow('Тема приложения',themes.find(([id])=>id===theme.getChoice())?.[1],'theme',themePicker,'theme'));target.append(group);
      const support=element('section','navigation-group');support.append(navigationRow('Поддержка','Контакт и документы','help',()=>getRouter().openDetail('support'),'support'));
      support.append(navigationRow('История уведомлений','Результаты ваших оповещений · Plus','history',()=>getRouter().openDetail('history'),'history'));
      support.append(navigationRow('Отчёты в боте','Команда /report и HTML-экспорт','posts',async event=>{
        const origin=event.currentTarget;
        let text='Отправьте /report в личном чате бота.';
        try{await navigator.clipboard.writeText('/report');text='Команда /report скопирована. Отправьте её в чате бота.';}catch{}
        dialog('Отчёты в боте',content=>content.append(element('p','',text)),{origin});
      },'report'));target.append(support);
    },
    dispose(){disposed=true;},
  };
}
