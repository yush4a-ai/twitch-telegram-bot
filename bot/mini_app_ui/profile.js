import {element,action,navigationRow,avatar} from './components.js';

const themes=[['telegram','Telegram'],['light','Светлая'],['dark','Тёмная']];
const date=value=>value?new Date(value*1000).toLocaleDateString('ru-RU',{dateStyle:'medium'}):'';

export function createProfileFeature(api,getRouter,theme) {
  let state=null,loading=false,error='',disposed=false;
  async function refresh(){
    if(loading||disposed)return;loading=true;
    try{const result=await api.post('/app/api/subscription/state');if(!disposed){state=result;error='';}}
    catch{if(!disposed)error='Не удалось обновить подписку. Попробуйте ещё раз.';}
    finally{loading=false;if(!disposed)getRouter().refresh();}
  }
  return {
    refresh,
    render(target){
      target.replaceChildren();target.dataset.profileState=state?'ready':error?'error':'loading';if(!state&&!loading&&!error)void refresh();target.append(element('h1','','Профиль'));
      const identity=api.user,account=element('section','profile-account');
      const copy=element('div','row-copy');copy.append(element('strong','profile-name',identity?.display_name||`Telegram ID ${identity?.id}`));
      copy.append(element('small','muted',identity?.username?`@${identity.username}`:`Telegram ID ${identity?.id}`));
      const active=state?.viewer.active||state?.streamer.active,product=state?.streamer.active?'Стример Plus':state?.viewer.active?'Зритель Plus':state?'Free':'Проверяем подписку…';
      copy.append(element('span','profile-plan',product));
      if(active){const until=date(state?.streamer.active?state.streamer.expires_at:state.viewer.expires_at);if(until)copy.append(element('small','muted',`До ${until}`));}
      const offer=action(active?'Моя подписка':'О тарифе',()=>getRouter().openDetail('subscription'),true);offer.classList.add('profile-offer');copy.append(offer);
      const mascot=element('img','profile-mascot');mascot.src='/app/mascot-cutout.png';mascot.alt='';mascot.width=112;mascot.height=112;
      account.append(avatar(identity?.display_name||identity?.id,identity?.avatar_url),copy,mascot);target.append(account);
      if(error)target.append(element('p','notice error',error),action('Обновить подписку',refresh,true));
      target.append(element('h2','profile-section-title','Настройки'));
      const settings=element('section','navigation-group profile-settings'),themeBox=element('div','profile-theme-box');themeBox.append(element('h3','','Тема приложения'));
      const choices=element('div','profile-theme');choices.setAttribute('role','group');choices.setAttribute('aria-label','Тема приложения');
      for(const [id,label] of themes){const button=action(label,()=>{theme.setChoice(id);getRouter().refresh();},true);button.setAttribute('aria-pressed',String(theme.getChoice()===id));button.dataset.focusKey=`profile-theme:${id}`;choices.append(button);}
      themeBox.append(choices);settings.append(themeBox,navigationRow('Уведомления','Тихие часы и сводка','notification',()=>getRouter().openDetail('viewer-settings'),'viewer-settings'));target.append(settings);
      const support=element('section','navigation-group profile-support');
      support.append(navigationRow('Поддержка','Помощь и документы','help',()=>getRouter().openDetail('support'),'support'));
      support.append(navigationRow('История уведомлений','Результаты оповещений · Зритель Plus','history',()=>getRouter().openDetail('history'),'history'));
      support.append(navigationRow('Отчёты об эфирах','Автоотчёт и формат после трансляции','chart',()=>getRouter().openDetail('reports'),'reports'));target.append(support);
    },
    dispose(){disposed=true;},
  };
}
