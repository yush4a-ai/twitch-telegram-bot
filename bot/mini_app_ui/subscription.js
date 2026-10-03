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
  function currentAccess(target) {
    if(!state.viewer.active&&!state.streamer.active)return;
    const group=element('section','plus-current');group.setAttribute('aria-label','Текущий доступ');
    for(const [key,id] of [['streamer','streamer_plus'],['viewer','viewer_plus']]){
      const access=state[key];if(!access.active)continue;
      if(key==='viewer'&&state.streamer.active&&state.viewer.sources.every(s=>s.product_id==='streamer_plus'))continue;
      const offer=catalog.products.find(p=>p.product_id===id), row=element('div','plus-current-row');
      row.append(element('strong','',offer.title),element('p','',`Активен${access.expires_at?' · до '+dateText(access.expires_at):''}`));
      if(key==='streamer'){
        row.append(element('p','muted',state.viewer.active?'Viewer Plus включён для вашего Telegram-аккаунта.':'Возможности зрителя пока не активны. Обратитесь в поддержку.'));
        if(!access.linked)row.append(element('p','muted','Подписка сохранена. Для публикаций подключите свой Twitch-канал.'));
        else if(!access.publishing_access)row.append(element('p','muted','Подписка связана с прежним Twitch-каналом. Проверьте подключение в разделе «Мой канал».'));
      }
      group.append(row);
    }
    group.append(element('p','muted','Автопродления нет. Срок доступа указан по данным сервера.'));target.append(group);
  }
  const onVisibility=()=>{const detail=getRouter().state.detail;if(!document.hidden&&(detail==='subscription'||detail?.name==='subscription'))void load({fresh:true});};
  document.addEventListener('visibilitychange',onVisibility);
  return {
    render(target, route) {
      target.replaceChildren();if(!requested)void load();
      target.append(element('h1','',state?.viewer.active||state?.streamer.active?'Моя подписка':'Тариф'));
      if(!state||!catalog){target.append(panel('Загружаем подписку',error||'Проверяем доступ и возможности.'));if(error)target.append(action('Повторить',()=>load({fresh:true})));return;}
      if(error){const notice=element('p','notice error',error);notice.setAttribute('role','alert');target.append(notice);}
      if(feedback){const notice=element('p','notice',feedback);notice.setAttribute('role','status');target.append(notice);}
      currentAccess(target);
      const secondary=route.detail?.name==='subscription'&&route.detail.id==='viewer_plus';
      const id=secondary?'viewer_plus':catalog.primary_products[route.mode];
      const offer=catalog.products.find(p=>p.product_id===id), access=state[id==='viewer_plus'?'viewer':'streamer'];
      const box=element('section','plus-offer');box.dataset.product=id;
      box.append(element('h2','',offer.title),element('p','lead',offer.value),element('p','subscription-price',`${offer.price_label} / месяц`));
      if(id==='viewer_plus'&&access.active&&state.viewer.sources.some(s=>s.product_id==='streamer_plus'))box.append(element('p','muted','Viewer Plus доступен в составе Streamer Plus.'));
      const benefits=element('div','plus-benefits');
      for(const block of offer.benefit_blocks){const row=element('div','plus-benefit');row.dataset.benefitBlock=block.feature_ids.join(',');const copy=element('div','row-copy');copy.append(element('strong','',block.title),element('small','muted',block.description));row.append(icon(block.icon),copy);benefits.append(row);}
      box.append(benefits);
      if(id==='streamer_plus')box.append(disclosure('Viewer Plus включён',id+':included',catalog.products.find(p=>p.product_id==='viewer_plus').feature_ids));
      box.append(action(`${access.active?'Продлить':'Оформить'} ${offer.title} — ${offer.price_label}`,()=>getRouter().openDetail({name:'purchase',id})));
      box.append(element('p','muted plus-renewal','1 месяц. Без автопродления.'));
      box.append(disclosure('Все возможности',id+':all',offer.feature_ids));
      if(id==='viewer_plus'&&state.viewer.test_trial_available){box.append(element('p','muted','Ознакомление на 7 дней. Один раз, без оплаты и автопродления.'));const trial=action('Попробовать 7 дней',startTrial,true);trial.disabled=busy||loading;box.append(trial);}
      else if(id==='viewer_plus'&&state.viewer.test_trial_used)box.append(element('p','muted',state.viewer.test_trial_active?`Ознакомление действует до ${dateText(state.viewer.test_trial_expires_at)}.`:'Ознакомление уже использовано.'));
      target.append(box);
      if(route.mode==='streamer'&&!secondary){const viewer=catalog.products.find(p=>p.product_id==='viewer_plus');target.append(navigationRow(`Тариф для зрителя — ${viewer.price_label}`,'','people',()=>getRouter().openDetail({name:'subscription',id:'viewer_plus'})));}
      const free=element('section','plus-free');free.append(element('h2','','Free'),element('p','muted',`До ${catalog.limits.free_streamers} стримеров, фото в уведомлениях, начало эфира и тихие часы. Подключение Twitch и Telegram-канала, обычный пост и HTML-отчёты доступны бесплатно.`));target.append(free);
      target.append(element('h2','section-head','Мои операции'));
      if(!state.history.length)target.append(element('p','muted','Операций пока нет. Здесь появятся ваши заказы и их статус.'));
      else{const history=element('div','navigation-group');for(const order of state.history){const title=catalog.products.find(p=>p.product_id===order.product)?.title||'Plus';history.append(navigationRow(title,`${orderStatus(order)} · ${dateText(order.created_at)}`,'history',()=>getRouter().openDetail({name:'purchase-order',id:order.order_id}),order.order_id));}target.append(history);}
      target.append(navigationRow('Документы и поддержка','','help',()=>getRouter().openDetail('support')));
      const update=action('Обновить статус',()=>load({fresh:true}),true);update.disabled=loading||busy;target.append(update);
    },
    refresh:load,
    dispose(){disposed=true;++generation;controller?.abort();document.removeEventListener('visibilitychange',onVisibility);},
  };
}
