import { ApiError } from './api.js';
import { element, panel, action, navigationRow, dialog } from './components.js';

export function createStreamerPostsFeature(api,getRouter,getProfile) {
  const states=new Map(),drafts=new Map(),requests=new Map(),feedback=new Map(),errors=new Map(),busy=new Set();
  let selected=null,disposed=false,sequence=0,previewTimer=null,previewController=null,previewSequence=0;
  let presetName='';
  const draftExamples=new Map(),videoDesired=new Map();let previewPendingKey=null;
  const refresh=()=>{if(!disposed)getRouter().refresh();};
  const key=id=>`ts-streamer-template-${getProfile().twitch_login}-${id}`;
  const clone=value=>JSON.parse(JSON.stringify(value));
  const copyDraft=saved=>({version:saved.version,headline:saved.headline,body:saved.body,
    buttons:[0,1].map(i=>saved.buttons[i]||{label:'',url:''})});
  function remember(id){try{api.storage.setItem(key(id),JSON.stringify(drafts.get(id)));}catch{}}
  function draft(id,saved){
    if(drafts.has(id))return drafts.get(id);
    let value=copyDraft(saved);
    try{const stored=JSON.parse(api.storage.getItem(key(id))||'null');
      if(stored&&Number.isSafeInteger(stored.version)&&stored.version>=0&&typeof stored.headline==='string'&&stored.headline.length<=60
          &&typeof stored.body==='string'&&stored.body.length<=140&&Array.isArray(stored.buttons)&&stored.buttons.length===2
          &&stored.buttons.every(b=>typeof b.label==='string'&&b.label.length<=24&&typeof b.url==='string'&&b.url.length<=512))value=stored;
    }catch{}
    drafts.set(id,value);return value;
  }
  async function load(id,{fresh=false}={}){
    if(disposed||!id)return;
    const old=requests.get(id);if(old&&!fresh)return old.promise;old?.controller.abort();
    const controller=new AbortController(),token=++sequence;errors.delete(id);
    const record={controller,token};requests.set(id,record);
    const promise=(async()=>{
      try{
        const options={signal:controller.signal};
        const [example,template,presets]=await Promise.all([
          api.post('/app/api/streamer/post-example',{chat_id:id},options),api.post('/app/api/streamer/template',{chat_id:id},options),api.post('/app/api/streamer/presets',{},options)]);
        let stats=null,compare=null;
        if(template.can_edit){const values=await Promise.allSettled([api.post('/app/api/streamer/stats',{},options),api.post('/app/api/streamer/stats/compare',{},options)]);
          stats=values[0].status==='fulfilled'?values[0].value:null;compare=values[1].status==='fulfilled'?values[1].value:null;}
        if(disposed||requests.get(id)?.token!==token)return;
        states.set(id,{example,template,presets:presets.presets,stats,compare});draft(id,template);
      }catch(cause){if(disposed||controller.signal.aborted||requests.get(id)?.token!==token)return;
        errors.set(id,cause instanceof ApiError&&cause.code==='verification_unavailable'?'Telegram не ответил. Права пока не подтверждены.'
          :cause instanceof ApiError&&(cause.status===401||cause.status===403)?'Доступ изменился. Проверьте подключение и права канала.':'Не удалось загрузить пост. Проверьте связь и повторите.');
        if(cause instanceof ApiError&&cause.status===403&&states.has(id))states.get(id).template.can_edit=false;
      }finally{if(requests.get(id)?.token===token){requests.delete(id);refresh();}}
    })();record.promise=promise;return promise;
  }
  const draftPayload=value=>({headline:value.headline,body:value.body,buttons:value.buttons.filter(b=>b.label||b.url)});
  const previewKey=id=>`${id}:${states.get(id)?.template.version}:${JSON.stringify(draftPayload(drafts.get(id)))}`;
  function cancelPreview(){clearTimeout(previewTimer);previewController?.abort();previewPendingKey=null;++previewSequence;}
  function schedulePreview(id){
    cancelPreview();const token=previewSequence,snapshot=clone(drafts.get(id)),key=previewKey(id);previewPendingKey=key;
    previewTimer=setTimeout(async()=>{
      const controller=new AbortController();previewController=controller;
      try{const value=await api.post('/app/api/streamer/post-example',{chat_id:id,draft:{headline:snapshot.headline,body:snapshot.body,buttons:snapshot.buttons.filter(b=>b.label||b.url)}},{signal:controller.signal});
        if(disposed||token!==previewSequence||selected!==id||previewKey(id)!==key)return;
        draftExamples.set(id,{key,value});
        const target=document.querySelector('[data-draft-preview]');if(target)renderPreview(target,value);
      }catch(cause){if(disposed||controller.signal.aborted||token!==previewSequence||selected!==id)return;
        const target=document.querySelector('[data-draft-preview]');if(target)target.replaceChildren(element('p','notice error',cause instanceof ApiError&&cause.status===400?'Проверьте текст и HTTPS-адреса кнопок.':'Не удалось обновить предпросмотр. Черновик сохранён.'));}
      finally{if(token===previewSequence)previewPendingKey=null;}
    },300);
  }
  async function mutation(id,path,values,onSuccess){
    if(disposed||busy.has(id))return;busy.add(id);cancelPreview();feedback.delete(id);refresh();
    try{const result=await api.post(`/app/api/streamer/${path}`,values);if(disposed)return;
      onSuccess?.(result);await load(id,{fresh:true});
    }catch(cause){if(disposed)return;
      if(cause instanceof ApiError&&cause.code==='name_taken')feedback.set(id,'Название уже занято. Выберите другое.');
      else if(cause instanceof ApiError&&cause.code==='preset_limit')feedback.set(id,'Достигнут предел сохранённых вариантов. Удалите ненужный.');
      else if(cause instanceof ApiError&&cause.status===409){feedback.set(id,'Оформление изменилось в другом окне. Ваш текст сохранён здесь; обновите версию перед повтором.');const state=states.get(id);if(state)state.conflict=true;}
      else feedback.set(id,cause instanceof ApiError&&cause.status===403?'Доступ изменился. Ваш черновик сохранён.'
        :cause instanceof ApiError&&cause.status===400?'Проверьте название, текст и HTTPS-адреса кнопок.':'Не удалось сохранить изменения. Черновик сохранён.');
      if(cause instanceof ApiError&&cause.status===403){const state=states.get(id);if(state)state.template.can_edit=false;refresh();await load(id,{fresh:true});}
    }finally{busy.delete(id);refresh();}
  }
  async function save(id){const value=clone(drafts.get(id));return mutation(id,'template',{chat_id:id,...value,buttons:value.buttons.filter(b=>b.label||b.url)},()=>{
    drafts.delete(id);try{api.storage.removeItem(key(id));}catch{}feedback.set(id,'Оформление сохранено');});}
  async function version(id){if(busy.has(id))return;busy.add(id);refresh();
    try{const current=await api.post('/app/api/streamer/template',{chat_id:id});if(disposed)return;
      if(!current.can_edit){feedback.set(id,'Доступ изменился. Ваш черновик сохранён.');await load(id,{fresh:true});return;}
      drafts.get(id).version=current.version;remember(id);states.get(id).conflict=false;
      feedback.set(id,'Актуальная версия загружена. Ваш текст сохранён; проверьте его и нажмите «Сохранить оформление».');
    }catch{feedback.set(id,'Не удалось получить актуальную версию. Ваш текст сохранён.');}
    finally{busy.delete(id);refresh();}
  }
  function renderPreview(target,example){target.replaceChildren();target.append(element('div','post-preview-text',example.text));
    const buttons=element('div','post-preview-buttons');for(const button of example.buttons)buttons.append(element('span','post-preview-button',button.label));target.append(buttons);}
  function preview(target,example,draftPreview=false){const box=element('section','post-example');if(draftPreview)box.dataset.draftPreview='';
    renderPreview(box,example);target.append(box);}
  function note(target,id){const text=feedback.get(id);if(text){const p=element('p','notice post-feedback',text);p.setAttribute('role','status');target.append(p);}}
  function button(label,id,callback,secondary=false){const b=action(label,callback,secondary);b.disabled=busy.has(id);return b;}
  function fields(target,id){const value=drafts.get(id),form=element('form','template-form');
    const field=(label,name,max,multiline=false)=>{const wrap=element('label','template-field',label),input=element(multiline?'textarea':'input','input');
      input.value=name.includes('.')?value.buttons[Number(name[0])][name.slice(2)]:value[name];input.maxLength=max;input.disabled=busy.has(id);input.dataset.focusKey=`post:${id}:${name}`;
      if(multiline)input.rows=3;input.addEventListener('input',()=>{if(name.includes('.'))value.buttons[Number(name[0])][name.slice(2)]=input.value;else value[name]=input.value;remember(id);schedulePreview(id);});wrap.append(input);return wrap;};
    form.append(field('Заголовок','headline',60),field('Текст','body',140,true));
    for(let i=0;i<2;i++)form.append(field(`Кнопка ${i+1} · название`,`${i}.label`,24),field(`Кнопка ${i+1} · HTTPS-адрес`,`${i}.url`,512));
    const submit=button('Сохранить оформление',id,()=>{});submit.type='submit';form.append(submit);form.addEventListener('submit',event=>{event.preventDefault();void save(id);});target.append(form);
  }
  function variants(target,id,state){
    target.append(element('p','lead','Сохранённый вариант не меняет пост, пока вы его не примените.'));
    if(state.template.can_edit){const field=element('label','template-field','Название варианта'),input=element('input','input');input.maxLength=40;input.value=presetName;input.disabled=busy.has(id);input.addEventListener('input',()=>presetName=input.value);field.append(input);target.append(field);
      target.append(button('Сохранить вариант',id,()=>{const value=drafts.get(id);return mutation(id,'presets/create',{name:presetName,headline:value.headline,body:value.body,buttons:value.buttons.filter(b=>b.label||b.url)},()=>{presetName='';feedback.set(id,'Вариант сохранён. Текущее оформление и опубликованные посты не изменились.');});}));}
    if(!state.presets.length)target.append(element('p','muted','Пока нет сохранённых вариантов.'));
    for(const preset of state.presets){const row=element('section','preset-row');row.append(element('strong','',preset.name),element('p','muted',preset.headline));
      const actions=element('div','actions');
      if(state.template.can_edit)actions.append(button('Применить',id,()=>mutation(id,'presets/apply',{preset_id:preset.id,chat_id:id,expected_version:drafts.get(id).version},()=>{drafts.delete(id);try{api.storage.removeItem(key(id));}catch{}feedback.set(id,`Вариант «${preset.name}» применён к будущим постам этого сообщества.`);})));
      actions.append(button('Удалить',id,event=>dialog('Удалить вариант?',(box,close)=>{
        box.append(element('p','',preset.name),action('Удалить вариант',()=>{close();void mutation(id,'presets/delete',{preset_id:preset.id},()=>feedback.set(id,'Вариант удалён. Текущее оформление сохранено.'));}),action('Отмена',close,true));
      },{origin:event.currentTarget}),true));row.append(actions);target.append(row);}
  }
  function render(target,route){
    const profile=getProfile(),detail=typeof route.detail==='string'?route.detail.split(':'):[];
    const name=detail[0],id=Number(detail[1]);
    if(id&&profile.communities.some(c=>c.chat_id===id))selected=id;
    if(!profile.communities.some(c=>c.chat_id===selected))selected=profile.communities[0]?.chat_id;
    const community=profile.communities.find(c=>c.chat_id===selected);
    const title={'post-editor':'Оформление поста','post-variants':'Сохранённые варианты','post-stats':'Статистика публикаций','post-video':'Видеопревью'}[name]||'Посты';
    target.append(element('p','eyebrow','Стример'),element('h1','',title));
    if(!profile.connected||!community){target.append(element('p','lead','Подключите Twitch и Telegram-канал в разделе «Мой канал».'),action('Мой канал',()=>getRouter().setTab('channel')));return;}
    target.append(element('p','lead',community.title));
    if(title==='Посты'&&profile.communities.length>1){const field=element('label','post-community-select','Сообщество'),select=element('select','input');select.setAttribute('aria-label','Сообщество');
      for(const c of profile.communities){const option=element('option','',c.title);option.value=String(c.chat_id);select.append(option);}select.value=String(selected);
      select.addEventListener('change',()=>{cancelPreview();selected=Number(select.value);refresh();});field.append(select);target.append(field);}
    const state=states.get(selected),error=errors.get(selected);
    if(error)target.append(element('p','notice error',error),button('Повторить',selected,()=>load(selected,{fresh:true}),true));
    if(!state){if(!requests.has(selected)&&!error){const current=selected;queueMicrotask(()=>{if(!disposed&&!states.has(current)&&!requests.has(current))void load(current);});}
      if(!error)target.append(element('div','status-panel','Загружаем пост…'));return;}
    note(target,selected);
    if(state.conflict)target.append(button('Обновить версию',selected,()=>version(selected),true));
    if(title==='Посты'){
      preview(target,state.example);target.append(element('p','notice','Предпросмотр. Сообщение в Telegram не отправляется.'));
      const list=element('div','list');
      for(const [label,glyph,routeName,detail] of [['Оформление','text','post-editor',state.template.can_edit?'Текст и две кнопки':'О тарифе'],['Видеопревью','video','post-video',state.example.animation_enabled?'Включено':'Фото'],['Сохранённые варианты','folder','post-variants',String(state.presets.length)],['Статистика публикаций','chart','post-stats',state.stats?`${state.stats.published_posts} за 30 дней`:'О тарифе']])
        list.append(navigationRow(label,detail,glyph,()=>{cancelPreview();getRouter().openDetail(`${routeName}:${selected}`);}));target.append(list);return;
    }
    if(!state.template.can_edit&&name!=='post-variants'){
      target.append(panel('Обычный пост доступен бесплатно','Текст, дополнительные кнопки и видео доступны с Streamer Plus. Сохранённые настройки останутся на месте.'),action('Тариф для стримера',()=>getRouter().openDetail('subscription'),true));return;}
    if(name==='post-editor'){
      const id=selected,key=previewKey(id),cached=draftExamples.get(id);
      fields(target,id);preview(target,cached?.key===key?cached.value:state.example,true);
      if(!busy.has(id)&&!requests.has(id)&&cached?.key!==key&&previewPendingKey!==key
          &&JSON.stringify(draftPayload(drafts.get(id)))!==JSON.stringify(draftPayload(copyDraft(state.template))))
        queueMicrotask(()=>{if(!disposed&&selected===id&&previewKey(id)===key&&previewPendingKey!==key)schedulePreview(id);});
      target.append(element('p','notice','Черновик меняет только предпросмотр. Сохраните оформление для будущих постов.'));return;}
    if(name==='post-variants'){variants(target,selected,state);return;}
    if(name==='post-video'){
      target.append(element('p','lead','Во время эфира бот обновляет короткое видео без звука в вашем посте.'));
      const label=element('label','switch-row'),input=element('input');input.type='checkbox';input.checked=videoDesired.get(selected)??state.example.animation_enabled;input.disabled=busy.has(selected)||!community.publishing;
      input.addEventListener('change',()=>{const id=selected,enabled=input.checked;if(busy.has(id))return;videoDesired.set(id,enabled);
        void mutation(id,'preview',{chat_id:id,enabled},()=>feedback.set(id,enabled?'Видеопревью включено':'Видеопревью выключено'))
          .finally(()=>{videoDesired.delete(id);refresh();});});
      label.append(input,element('span','','Включить видеопревью'));target.append(label);
      if(videoDesired.has(selected))target.append(element('p','notice','Сохраняем выбор…'));
      if(!community.publishing)target.append(element('p','notice','Сначала включите публикации в разделе «Мой канал».'));return;}
    if(name==='post-stats'){
      const summary=element('section','publication-summary');summary.append(element('span','muted','Подтверждённые публикации за 30 дней'));
      if(state.stats){const count=element('strong','',String(state.stats.published_posts));count.dataset.publishedPosts='';summary.append(count);}else summary.append(element('p','notice','Статистика временно недоступна.'));
      target.append(summary,element('p','lead','Бот учитывает опубликованные посты. Просмотры Telegram не измеряются.'));
      if(state.compare)target.append(panel('Последние 7 дней / предыдущие 7',`${state.compare.current_posts} / ${state.compare.previous_posts} публикаций`));
      target.append(element('p','notice','Переходы на Twitch · В разработке'));}
  }
  return {render,refresh(){for(const id of states.keys())void load(id,{fresh:true});},dispose(){disposed=true;cancelPreview();for(const request of requests.values())request.controller.abort();}};
}
