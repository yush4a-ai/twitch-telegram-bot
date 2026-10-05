import {element, panel, action, navigationRow} from './components.js';
import {ApiError} from './api.js';
import {dateText, orderStatus} from './subscription.js';

export function createPurchaseFeature(api,getRouter,telegram) {
  let catalog=null, error='', requested=false, loading=false, disposed=false, generation=0, controller=null;
  const purchases=new Map(), orders=new Map(), requests=new Set();
  const refresh=()=>{if(!disposed)getRouter().refresh();};
  function purchase(id){if(!purchases.has(id))purchases.set(id,{busy:false,method:null,message:'',failed:false,requestKey:null});return purchases.get(id);}
  async function load() {
    if(loading||disposed)return;requested=true;loading=true;controller=new AbortController();
    const token=++generation, signal=controller.signal, timer=setTimeout(()=>controller?.signal===signal&&controller.abort(),5000);
    try{const result=await api.post('/app/api/subscription/catalog',{}, {signal});if(token===generation&&!disposed){catalog=result;error='';}}
    catch{if(!disposed)error='Не удалось загрузить способы оплаты. Попробуйте ещё раз.';}
    finally{clearTimeout(timer);if(token===generation&&!disposed){loading=false;refresh();}}
  }
  async function choose(id, method) {
    const current=purchase(id);if(current.busy||disposed)return;
    if(current.method!==method)current.requestKey=null;
    current.busy=true;current.method=method;current.message='';current.failed=false;
    current.requestKey ||= crypto.randomUUID();refresh();
    const request=new AbortController(), timer=setTimeout(()=>request.abort(),20000);requests.add(request);
    try{
      const result=await api.post('/app/api/purchase/prepare',{product:id,method,request_key:current.requestKey},{signal:request.signal});
      clearTimeout(timer);
      if(result?.state==='pending'&&typeof result.payment_url==='string'&&result.payment_url){
        if(method==='stars'){
          current.message='Открыли окно оплаты Telegram. После оплаты вернитесь сюда.';
          const status=await telegram.openInvoice(result.payment_url);
          if(status==='paid')current.message='Оплата прошла. Обновляем доступ…';
          else if(status==='pending')current.message='Оплата обрабатывается. Нажмите «Обновить статус» через минуту.';
          else if(status==='cancelled')current.message='Оплата отменена. Можно попробовать снова.';
          else if(status==='failed'){current.message='Оплата не прошла. Попробуйте другой способ.';current.failed=true;}
          else current.message='Если окно оплаты не открылось, обновите статус операции.';
        }else{
          telegram.openLink(result.payment_url);
          current.message='Открыли страницу оплаты. После оплаты вернитесь в приложение.';
        }
        if(typeof result.order_id==='string'&&result.order_id)getRouter().openDetail({name:'purchase-order',id:result.order_id});
      }else{
        current.message=typeof result?.message==='string'?result.message:'Оплата сейчас недоступна.';
        current.failed=true;
      }
    }catch(cause){
      if(cause instanceof ApiError&&cause.status===503&&cause.data?.state==='unavailable'&&cause.data?.payment_request_created===false&&typeof cause.data?.message==='string')current.message=cause.data.message;
      else{current.message='Нет связи. Попробуйте ещё раз.';current.failed=true;}
    }finally{clearTimeout(timer);requests.delete(request);current.busy=false;refresh();}
  }
  async function loadOrder(id,{fresh=false}={}) {
    const current=orders.get(id)||{loading:false,value:null,error:'',token:0,controller:null};orders.set(id,current);
    if(disposed||(current.loading&&!fresh))return;
    current.controller?.abort();current.loading=true;
    const token=++current.token;
    const request=new AbortController(), timer=setTimeout(()=>request.abort(),5000);requests.add(request);
    current.controller=request;
    try{const value=await api.post('/app/api/purchase/state',{order_id:id},{signal:request.signal});if(current.token===token&&!disposed){current.value=value;current.error='';}}
    catch{if(current.token===token&&!disposed)current.error='Не удалось загрузить операцию. Проверьте связь и попробуйте снова.';}
    finally{clearTimeout(timer);requests.delete(request);if(current.token===token&&!disposed){current.loading=false;current.controller=null;refresh();}}
  }
  function renderOrder(target,id) {
    target.append(element('h1','','Моя операция'));
    if(!orders.has(id))void loadOrder(id);
    const current=orders.get(id);
    if(current.loading&&current.value){const note=element('p','notice','Обновляем статус…');note.setAttribute('role','status');target.append(note);}
    if(current.error){target.append(panel('Операция недоступна',current.error),action('Повторить',()=>loadOrder(id)));return;}
    if(!current.value){target.append(panel('Проверяем статус','Загружаем вашу операцию.'));return;}
    const order=current.value, offer=catalog.products.find(p=>p.product_id===order.product);
    target.append(element('h2','',offer?.title||'Plus'),element('p','',orderStatus(order)));
    if(order.monetary)target.append(element('p','muted',catalog.methods.find(m=>m.id===order.method)?.title||'Способ оплаты'));
    target.append(element('p','muted',`Создана ${dateText(order.created_at)}`));
    if(order.access_expires_at)target.append(element('p','muted',`Срок доступа по операции: до ${dateText(order.access_expires_at)}`));
    target.append(element('p','',order.effective_access.viewer?'Возможности Зритель Plus сейчас активны.':'Возможности Зритель Plus сейчас не активны.'));
    const update=action('Обновить статус',()=>loadOrder(id),true);update.disabled=current.loading;target.append(update,navigationRow('Документы и поддержка','','help',()=>getRouter().openDetail('support')));
  }
  const visibility=()=>{const route=getRouter().state;if(!document.hidden&&route.detail?.name==='purchase-order')void loadOrder(route.detail.id,{fresh:true});};
  document.addEventListener('visibilitychange',visibility);
  return {
    refresh:load,
    refreshOrder:id=>loadOrder(id,{fresh:true}),
    render(target,route) {
      target.replaceChildren();if(!requested)void load();
      if(!catalog){target.append(element('h1','','Как оплатить?'),panel('Способы оплаты',error||'Загружаем тариф и цену.'));if(error)target.append(action('Повторить',load));return;}
      if(route.detail?.name==='purchase-order'){renderOrder(target,route.detail.id);return;}
      const id=route.detail?.id, offer=catalog.products.find(p=>p.product_id===id);
      if(!offer){target.append(element('h1','','Выберите тариф'),action('Посмотреть тариф',()=>getRouter().openDetail('subscription')));return;}
      const current=purchase(id);
      target.append(element('h1','','Как оплатить?'),element('h2','',offer.title),element('p','subscription-price',`${offer.price_label} / месяц`));
      target.append(element('p','muted','1 месяц. Без автопродления.'),element('p','','Telegram Stars: через Telegram.'),element('p','','СБП и банковская карта: через Platega.'));
      if(catalog.methods.length && catalog.methods.every(method=>offer.method_readiness?.[method.id]?.enabled===false)) {
        const note=element('p','purchase-availability','Оформление Plus пока недоступно. Бесплатные функции работают.');
        note.dataset.paymentAvailability='unavailable';target.append(note);
      }
      const methods=element('div','navigation-group purchase-methods');
      for(const method of catalog.methods){const button=navigationRow(method.title,method.provider==='platega'?'Внешняя оплата через Platega':'Оплата в Telegram','buttons',()=>choose(id,method.id));button.disabled=current.busy;button.setAttribute('aria-pressed',String(method.id===current.method));methods.append(button);}target.append(methods);
      if(current.busy){const note=element('p','notice','Проверяем доступность оплаты…');note.setAttribute('role','status');target.append(note);}
      if(current.message){const note=element('p',current.failed?'notice error':'notice',current.message);note.setAttribute('role',current.failed?'alert':'status');target.append(note);if(current.failed)target.append(action('Повторить',()=>choose(id,current.method),true));}
      target.append(navigationRow('Документы и поддержка','','help',()=>getRouter().openDetail('support')));
    },
    dispose(){disposed=true;++generation;controller?.abort();for(const request of requests)request.abort();document.removeEventListener('visibilitychange',visibility);},
  };
}
