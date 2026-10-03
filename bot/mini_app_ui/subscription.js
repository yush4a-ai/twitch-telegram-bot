import { element, panel, action, icon, navigationRow } from './components.js';
import { ApiError } from './api.js';

export const dateText = value => value ? new Date(value * 1000).toLocaleString('ru-RU', {dateStyle:'medium', timeStyle:'short'}) : '';
export function orderStatus(order) {
  if (!order.monetary) return {pending:'Доступ ещё не предоставлен', paid:'Доступ предоставлен без оплаты', cancelled:'Отменён', expired:'Срок истёк', refunded:'Доступ отозван'}[order.status] || 'Статус неизвестен';
  if(order.financial_status==='pending'&&order.status==='expired')return 'Время оплаты истекло';
  return {pending:'Ожидает оплаты', confirmed:'Оплата подтверждена', refunded:'Возврат подтверждён', canceled:'Отменён', expired:'Время оплаты истекло', manual_review:'Проверяем платёж'}[order.financial_status] || 'Статус неизвестен';
}

export function createSubscriptionFeature(api, getRouter, onAccessChanged) {
  let state=null, catalog=null, loading=false, requested=false, disposed=false, busy=false;
  let generation=0, controller=null, pending=null, feedback='', error='';
  const disclosures=new Map();
  const refresh=()=>{if(!disposed)getRouter().refresh();};
  async function load({fresh=false}={}) {
    if(disposed)return;
    if(pending&&!fresh)return pending;
    controller?.abort();controller=new AbortController();
    const signal=controller.signal, token=++generation;
    const timer=setTimeout(()=>controller?.signal===signal&&controller.abort(),5000);
    requested=true;loading=true;
    pending=(async()=>{
      try {
        const [status, offers]=await Promise.all([api.post('/app/api/subscription/state',{}, {signal}),api.post('/app/api/subscription/catalog',{}, {signal})]);
        if(token===generation&&!disposed){state=status;catalog=offers;error='';}
      }catch{if(token===generation&&!disposed)error=state?'Нет связи. Показан последний загруженный статус.':'Не удалось загрузить подписку. Попробуйте ещё раз.';}
      finally{clearTimeout(timer);if(token===generation&&!disposed){loading=false;pending=null;refresh();}}
    })();
    return pending;
  }
  async function startTrial() {
    if(busy||disposed)return;busy=true;feedback='';refresh();
    try{
      const result=await api.post('/app/api/subscription/test-trial');
      if(disposed)return;
      feedback=result.started_now?'Ознакомление на 7 дней включено. Без оплаты и автопродления.':'Ознакомление уже включено. Срок не изменился.';
      await load({fresh:true});onAccessChanged();
    }catch(cause){if(!disposed){feedback=cause instanceof ApiError&&cause.code==='trial_used'?'Ознакомление уже использовано. Бесплатные возможности продолжают работать.':cause instanceof ApiError&&cause.code==='plus_active'?'Plus уже активен. Ознакомление остаётся доступным позже.':'Не удалось включить ознакомление. Попробуйте ещё раз.';await load({fresh:true});}}
    finally{busy=false;refresh();}
  }
  function featureList(ids) {
    const list=element('ul','plus-feature-list');
    for(const id of ids){const feature=catalog.features[id];if(!feature)continue;const item=element('li','');item.append(element('strong','',feature.title),element('p','muted',feature.description));list.append(item);}
    return list;
  }
  function disclosure(label, key, ids) {
    const details=element('details','plus-disclosure');details.open=disclosures.get(key)||false;
    details.append(element('summary','',label),featureList(ids));
    details.addEventListener('toggle',()=>disclosures.set(key,details.open));return details;
  }
  function compactDetails(label, key, ...children) {
    const details=element('details','plus-disclosure');details.open=disclosures.get(key)||false;
    details.append(element('summary','',label),...children);
    details.addEventListener('toggle',()=>disclosures.set(key,details.open));return details;
  }
  function benefitCopy(block) {
    const key=block.feature_ids[0];
    return {
      viewer_channels:[`До ${catalog.limits.plus_streamers} стримеров`,`В Free — ${catalog.limits.free_streamers}`],
      viewer_video:[`Видео от ${catalog.limits.video_slots} стримеров`,'Вы выбираете'],
      viewer_filters:['Точные уведомления','Фильтры и напоминания'],
      viewer_folders:['Папки и история','Порядок в подписках'],
      streamer_video:['Видеопревью','Вместо фото в посте'],
      streamer_text:['Ваш текст','И оформление поста'],
      streamer_buttons:['Кнопки и варианты','Ссылки и шаблоны'],
      streamer_publication_stats:['Статистика постов','Публикации и сравнение'],
    }[key]||[block.title,block.description];
  }
  function currentAccess(target) {
    if(!state.viewer.active&&!state.streamer.active)return;
    const group=element('section','plus-current');group.setAttribute('aria-label','Текущий доступ');
    for(const [key,id] of [['streamer','streamer_plus'],['viewer','viewer_plus']]){
      const access=state[key];if(!access.active)continue;
      if(key==='viewer'&&state.streamer.active&&state.viewer.sources.every(s=>s.product_id==='streamer_plus'))continue;
      const offer=catalog.products.find(p=>p.product_id===id), row=element('div','plus-current-row');
      row.append(element('strong','',offer.title),element('p','',`Активен${access.expires_at?' · до '+dateText(access.expires_at):''}`));
      if(key==='streamer'){
        row.append(element('p','muted',state.viewer.active?'Зритель Plus включён для вашего Telegram-аккаунта.':'Возможности зрителя пока не активны. Обратитесь в поддержку.'));
        if(!access.linked)row.append(element('p','muted','Подписка сохранена. Для публикаций подключите свой Twitch-канал.'));
        else if(!access.publishing_access)row.append(element('p','muted','Подписка связана с прежним Twitch-каналом. Проверьте подключение в разделе «Мой канал».'));
      }
      group.append(row);
    }
    group.append(element('p','muted','Автопродления нет. Срок доступа указан по данным сервера.'));target.append(group);
  }
  function featureDemo(featureId) {
    const demo=element('div','plus-example');demo.setAttribute('aria-label','Демонстрационный пример');
    demo.append(element('small','plus-example-label','Пример'));
    if(featureId.endsWith('_video')){
      demo.classList.add('plus-video-example');demo.append(icon('video'),element('p','','Видеопример ещё не добавлен'));
      return demo;
    }
    const examples={
      viewer_channels:['До 50 стримеров',`До ${catalog.limits.plus_streamers} стримеров`,'Показать лимит Plus'],
      viewer_filters:['Все эфиры стримера','Только Minecraft · без слова «повтор»','Показать фильтр'],
      viewer_categories:['Категория: Just Chatting','Категория сменилась на Minecraft → уведомление','Показать смену категории'],
      viewer_reminders:['Вы выбрали текущий эфир','Напоминание через 15 минут','Показать напоминание'],
      viewer_folders:['Alpha · Beta · Gamma','Игры: Alpha, Beta · Общение: Gamma','Разложить по папкам'],
      viewer_history:['Эфир начался','Уведомление отправлено · 12:30','Показать событие'],
      streamer_text:['Стример начал эфир','Сегодня строим новый мир. Заходи в чат!','Изменить текст'],
      streamer_buttons:['Смотреть эфир','Смотреть эфир · Наш чат','Добавить кнопку'],
      streamer_presets:['Оформление «Обычный эфир»','Оформление «Совместный эфир»','Сменить вариант'],
      streamer_publication_stats:['7 дней: 4 публикации','Предыдущие 7 дней: 3 публикации','Сравнить периоды'],
    };
    const example=examples[featureId];if(!example)return demo;
    const result=element('p','plus-example-result',example[0]);result.setAttribute('role','status');
    let changed=false;
    const button=action(example[2],()=>{changed=!changed;result.textContent=example[changed?1:0];result.classList.remove('plus-example-change');requestAnimationFrame(()=>result.classList.add('plus-example-change'));button.textContent=changed?'Ещё раз':example[2];},true);
    demo.append(result,button);return demo;
  }
  function renderDetails(target,offer) {
    target.append(element('h2','plus-details-title',offer.title),element('p','lead','Наглядные примеры. Они не меняют ваши настройки.'));
    for(const featureId of offer.feature_ids){
      const feature=catalog.features[featureId];if(!feature)continue;
      const section=element('section','plus-feature-example');section.dataset.plusFeature=featureId;
      section.append(element('h2','',feature.title),element('p','muted',feature.description),featureDemo(featureId));target.append(section);
    }
    if(offer.product_id==='streamer_plus'){
      target.append(disclosure('Зритель Plus тоже включён','details:included',catalog.products.find(p=>p.product_id==='viewer_plus').feature_ids));
    }
    target.append(action('К тарифу',()=>getRouter().back(),true));
  }
  const onVisibility=()=>{const detail=getRouter().state.detail;if(!document.hidden&&(detail==='subscription'||detail?.name==='subscription'))void load({fresh:true});};
  document.addEventListener('visibilitychange',onVisibility);
  return {
    render(target, route) {
      target.replaceChildren();if(!requested)void load();
      const detailsView=route.detail?.name==='subscription'&&route.detail.id.endsWith(':details');
      target.append(element('h1','',detailsView?'Возможности Plus':state?.viewer.active||state?.streamer.active?'Моя подписка':'Тариф'));
      if(!state||!catalog){target.append(panel('Загружаем подписку',error||'Проверяем доступ и возможности.'));if(error)target.append(action('Повторить',()=>load({fresh:true})));return;}
      if(error){const notice=element('p','notice error',error);notice.setAttribute('role','alert');target.append(notice);}
      if(feedback){const notice=element('p','notice',feedback);notice.setAttribute('role','status');target.append(notice);}
      const secondary=route.detail?.name==='subscription'&&route.detail.id.startsWith('viewer_plus');
      const id=secondary?'viewer_plus':catalog.primary_products[route.mode];
      const offer=catalog.products.find(p=>p.product_id===id), access=state[id==='viewer_plus'?'viewer':'streamer'];
      if(detailsView){renderDetails(target,offer);return;}
      currentAccess(target);
      const box=element('section','plus-offer');box.dataset.product=id;
      const hero=element('div','plus-hero'),copy=element('div','plus-hero-copy');
      copy.append(element('h2','plus-product-name',offer.title),element('p','plus-headline',id==='viewer_plus'?'Twitch по вашим правилам':'Посты в стиле вашего канала'));
      const mascot=element('img','plus-mascot');mascot.src='/app/plus-mascot.png';mascot.alt='';mascot.width=180;mascot.height=180;mascot.decoding='async';
      hero.append(copy,mascot);box.append(hero);
      if(id==='viewer_plus'&&access.active&&state.viewer.sources.some(s=>s.product_id==='streamer_plus'))box.append(element('p','muted','Зритель Plus доступен в составе Стример Plus.'));
      const benefits=element('div','plus-benefits');
      for(const block of offer.benefit_blocks){const row=element('div','plus-benefit');row.dataset.benefitBlock=block.feature_ids.join(',');const copy=element('div','row-copy'),[title,description]=benefitCopy(block);copy.append(element('strong','',title),element('small','muted',description));row.append(icon(block.icon),copy);benefits.append(row);}
      box.append(benefits);
      const purchase=element('div','plus-purchase');
      const price=element('p','subscription-price');price.append(element('strong','',offer.price_label),element('span','',' / месяц'));
      purchase.append(price,element('p','muted plus-renewal','1 месяц. Без автопродления.'));
      const unavailable=Object.values(offer.method_readiness||{}).length>0&&Object.values(offer.method_readiness).every(method=>!method.enabled);
      const buy=action(unavailable?'Способы оплаты':`${access.active?'Продлить':'Оформить'} ${offer.title}`,()=>getRouter().openDetail({name:'purchase',id}));buy.classList.add('plus-purchase-button');
      purchase.append(buy);
      if(unavailable)purchase.append(element('p','plus-payment-note','Оформление пока недоступно'));
      box.append(purchase);
      const more=action('Подробнее о Plus',()=>getRouter().openDetail({name:'subscription',id:id+':details'}),true);more.classList.add('plus-more');box.append(more);
      if(id==='streamer_plus')box.append(disclosure('Зритель Plus включён',id+':included',catalog.products.find(p=>p.product_id==='viewer_plus').feature_ids));
      if(id==='viewer_plus'&&state.viewer.test_trial_available){box.append(element('p','muted','Ознакомление на 7 дней. Один раз, без оплаты и автопродления.'));const trial=action('Попробовать 7 дней',startTrial,true);trial.disabled=busy||loading;box.append(trial);}
      else if(id==='viewer_plus'&&state.viewer.test_trial_used)box.append(element('p','muted',state.viewer.test_trial_active?`Ознакомление действует до ${dateText(state.viewer.test_trial_expires_at)}.`:'Ознакомление уже использовано.'));
      target.append(box);
      if(route.mode==='streamer'&&!secondary){const viewer=catalog.products.find(p=>p.product_id==='viewer_plus');target.append(navigationRow(`Тариф для зрителя — ${viewer.price_label}`,'','people',()=>getRouter().openDetail({name:'subscription',id:'viewer_plus'})));}
      const free=element('p','muted plus-details-copy',`До ${catalog.limits.free_streamers} стримеров, фото в уведомлениях, начало эфира и тихие часы. Подключение Twitch и Telegram-канала, обычный пост и HTML-отчёты доступны бесплатно.`);
      target.append(compactDetails('Что остаётся бесплатно','free',free));
      const history=element('div','navigation-group');
      if(!state.history.length)history.append(element('p','muted plus-details-copy','Операций пока нет. Здесь появятся ваши заказы и их статус.'));
      else{for(const order of state.history){const title=catalog.products.find(p=>p.product_id===order.product)?.title||'Plus';history.append(navigationRow(title,`${orderStatus(order)} · ${dateText(order.created_at)}`,'history',()=>getRouter().openDetail({name:'purchase-order',id:order.order_id}),order.order_id));}}
      target.append(compactDetails('Мои операции','history',history));
      target.append(navigationRow('Документы и поддержка','','help',()=>getRouter().openDetail('support')));
      const update=action('Обновить статус',()=>load({fresh:true}),true);update.disabled=loading||busy;target.append(update);
    },
    refresh:load,
    dispose(){disposed=true;++generation;controller?.abort();document.removeEventListener('visibilitychange',onVisibility);},
  };
}
