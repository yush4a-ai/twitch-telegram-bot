import {element,action,panel,navigationRow} from './components.js';

export function createReportsFeature(api,getRouter){
  let mode=null,data=null,error='',loading=false,disposed=false,generation=0,controller=null;
  const drafts=new Map(),feedback=new Map(),saving=new Set();
  const key=item=>`${item.chat_id}:${item.login||''}`;
  const refresh=()=>{if(!disposed)getRouter().refresh();};
  const recipient={self:'В ваш личный чат с ботом',channel:'В этот Telegram-канал',linked_private:'В привязанный личный чат',none:'Получатель не подключён'};
  async function load(next){
    mode=next;const version=++generation;controller?.abort();controller=new AbortController();const signal=controller.signal;
    loading=true;error='';
    try{const result=await api.post(`/app/api/${next}/reports`,{}, {signal});if(!disposed&&version===generation)data=result.items;}
    catch(cause){if(!disposed&&version===generation&&!signal.aborted)error=cause?.status===401||cause?.status===403?'Не удалось подтвердить доступ. Откройте приложение из чата бота.':'Не удалось загрузить настройки отчётов. Попробуйте ещё раз.';}
    finally{if(!disposed&&version===generation){loading=false;refresh();}}
  }
  function renderItem(target,item){
    const id=key(item),draftKey=mode+':'+id;
    target.append(element('h1','','Отчёты об эфирах'),element('p','lead',item.title));
    if(!item.available){target.append(panel('Настройки недоступны',item.permission_status==='network_error'?'Telegram не ответил. Повторите проверку позже.':item.permission_status?'Проверьте ваши права и права бота в канале.':'Для этого канала пока нет активной записи отслеживания. Откройте его настройки подключения.'));return;}
    if(!drafts.has(draftKey))drafts.set(draftKey,{enabled:item.enabled,format:item.format});const draft=drafts.get(draftKey);
    const box=element('section','settings-group feature-panel'),label=element('label','switch-row'),toggle=element('input','');toggle.type='checkbox';toggle.checked=draft.enabled;toggle.disabled=saving.has(draftKey);toggle.addEventListener('change',()=>draft.enabled=toggle.checked);label.append(toggle,element('span','','Автоотчёт после эфира'));box.append(label);
    const formatLabel=element('label','report-format','Формат отчёта'),format=element('select','input');format.setAttribute('aria-label','Формат отчёта');for(const [value,title]of [['brief','Краткий текст'],['full','Текст + HTML']]){const option=element('option','',title);option.value=value;format.append(option);}format.value=draft.format;format.disabled=saving.has(draftKey);format.addEventListener('change',()=>draft.format=format.value);formatLabel.append(format);box.append(formatLabel);
    box.append(element('p','muted',`Получатель: ${recipient[item.recipient]||recipient.none}.`));
    box.append(element('p','muted','Итог со статистикой приходит после окончательного завершения эфира, обычно не раньше чем через 30 минут. Тихие часы могут отложить доставку.'));
    const save=action(saving.has(draftKey)?'Сохраняем…':'Сохранить',async()=>{
      if(saving.has(draftKey))return;const requestMode=mode,version=generation;saving.add(draftKey);feedback.delete(draftKey);refresh();
      try{const result=await api.post(`/app/api/${requestMode}/reports/save`,{...(requestMode==='viewer'?{login:item.login}:{chat_id:item.chat_id}),enabled:draft.enabled,format:draft.format});if(disposed||version!==generation)return;data=data.map(row=>key(row)===id?result.item:row);feedback.set(draftKey,'Настройки отчёта сохранены');}
      catch(cause){if(!disposed&&version===generation)feedback.set(draftKey,cause?.status===403?'Права изменились. Проверьте доступ к каналу.':'Не удалось сохранить. Проверьте связь и повторите.');}
      finally{saving.delete(draftKey);refresh();}
    });save.disabled=saving.has(draftKey);box.append(save);
    if(feedback.has(draftKey)){const note=element('p','notice',feedback.get(draftKey));note.setAttribute('role','status');box.append(note);}target.append(box);
  }
  return {
    render(target,route){
      if(mode!==route.mode){data=null;drafts.clear();feedback.clear();void load(route.mode);}
      target.replaceChildren();
      if(!data){target.append(element('h1','','Отчёты об эфирах'),element('p','lead',error||'Загружаем настройки…'));if(error&&!loading)target.append(action('Повторить',()=>load(route.mode)));return;}
      const selected=typeof route.detail==='object'?data.find(item=>key(item)===route.detail.id):null;
      if(selected){renderItem(target,selected);return;}
      target.append(element('h1','','Отчёты об эфирах'),element('p','lead','Автоматическая статистика после трансляции. Настройте отдельно для каждого канала.'));
      if(!data.length){target.append(panel('Пока нет каналов',mode==='viewer'?'Сначала добавьте стримера в свои подписки.':'Сначала подключите свой Telegram-канал.'));return;}
      const list=element('section','navigation-group');for(const item of data)list.append(navigationRow(item.title,item.available?(item.enabled?'Автоотчёт включён':'Автоотчёт выключен'):'Настройки недоступны','chart',()=>getRouter().openDetail({name:'reports',id:key(item)})));target.append(list);
    },
    deactivate(){if(mode===null)return;mode=null;data=null;drafts.clear();feedback.clear();++generation;controller?.abort();loading=false;},
    dispose(){disposed=true;++generation;controller?.abort();},
  };
}
