import {element, panel, action, navigationRow} from './components.js';
import {ApiError} from './api.js';
import {dateText, orderStatus} from './subscription.js';
import {PAYMENT_STATUS_TEXT, describeFailure} from './failures.js';

// Ключ запроса и заказ последней операции переживают перезагрузку приложения
// (истёкшая подпись Telegram перезагружает его сама), поэтому лежат в
// sessionStorage: операция живёт одну сессию и не должна переезжать в постоянное
// хранилище. Сервер идемпотентен по ключу, поэтому повтор не создаёт второй заказ.
const OPERATION_KEY='ts-app-purchase-operation';
const OPERATION_TTL=6*60*60*1000;
const ORDER_ID=/^[0-9a-f]{32}$/;
const REQUEST_KEY=/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

const sessionStore=()=>{try{return window.sessionStorage;}catch{return null;}};
function readOperation(){
  const raw=sessionStore()?.getItem(OPERATION_KEY);
  if(!raw)return null;
  try{
    const value=JSON.parse(raw);
    if(!value||typeof value!=='object')return null;
    if(typeof value.request_key!=='string'||!REQUEST_KEY.test(value.request_key))return null;
    if(typeof value.order_id!=='string'||!ORDER_ID.test(value.order_id))value.order_id='';
    if(typeof value.payment_url!=='string')value.payment_url='';
    return value;
  }catch{return null;}
}
function writeOperation(patch){
  const storage=sessionStore();if(!storage)return null;
  const value={...readOperation(),...patch};
  try{storage.setItem(OPERATION_KEY,JSON.stringify(value));}catch{return null;}
  return value;
}
function clearOperation(){try{sessionStore()?.removeItem(OPERATION_KEY);}catch{}}

export function createPurchaseFeature(api,getRouter,telegram) {
  let catalog=null, error='', requested=false, loading=false, disposed=false, generation=0, controller=null;
  let catalogRetry=false;
  const purchases=new Map(), orders=new Map(), requests=new Set();
  const refresh=()=>{if(!disposed)getRouter().refresh();};
  function purchase(id){if(!purchases.has(id))purchases.set(id,{busy:false,method:null,message:'',failed:false,retry:false,requestKey:null});return purchases.get(id);}
  // Запись об операции принадлежит только тому, кто её начал.
  function operation(){const value=readOperation();if(!value)return null;const user=api.user?.id;if(value.user_id&&user&&value.user_id!==user)return null;return value;}
  async function load() {
    if(loading||disposed)return;requested=true;loading=true;controller=new AbortController();
    const token=++generation, signal=controller.signal, timer=setTimeout(()=>controller?.signal===signal&&controller.abort(),5000);
    try{const result=await api.post('/app/api/subscription/catalog',{}, {signal});if(token===generation&&!disposed){catalog=result;error='';catalogRetry=false;}}
    catch(cause){if(!disposed){const failure=describeFailure(cause);error=`Не удалось загрузить способы оплаты. ${failure.message}`;catalogRetry=failure.retry;}}
    finally{clearTimeout(timer);if(token===generation&&!disposed){loading=false;refresh();}}
  }
  function paymentState(result){
    // Сервер вернул состояние операции без ссылки: заказ существует, но ссылка
    // ещё не готова (сбой у провайдера) либо требует проверки человеком.
    if(result?.state==='creation_unknown'||result?.state==='manual_review'){
      return result.state==='manual_review'?'Платёж проверяется. Мы сообщим о результате в чате с ботом.':'Счёт готовится. Обновите статус операции через минуту.';
    }
    return typeof result?.message==='string'&&result.message?result.message:'Оплата сейчас недоступна.';
  }
  function rememberOrder(orderId){
    if(!ORDER_ID.test(orderId||''))return;
    writeOperation({order_id:orderId,finished:false,saved_at:Date.now(),user_id:api.user?.id||null});
  }
  async function openPaymentWindow(url,method,current){
    if(method==='stars'){
      current.message='Открыли окно оплаты Telegram. После оплаты вернитесь сюда.';refresh();
      const status=await telegram.openInvoice(url);
      if(disposed)return;
      if(status==='paid')current.message='Оплата прошла. Обновляем доступ…';
      else if(status==='pending')current.message='Оплата обрабатывается. Нажмите «Обновить статус» через минуту.';
      else if(status==='cancelled')current.message='Оплата отменена. Можно попробовать снова.';
      else if(status==='failed'){current.message='Оплата не прошла. Попробуйте другой способ.';current.failed=true;}
      else current.message='Если окно оплаты не открылось, обновите статус операции.';
    }else{
      telegram.openLink(url);
      current.message='Открыли страницу оплаты. После оплаты вернитесь в приложение.';
    }
    refresh();
  }
  async function openCheckout(result,method,current){
    const saved=operation();
    if(saved&&saved.order_id===result.order_id&&saved.link_opened){
      // Ссылка уже открывалась: повторно окно оплаты не открываем, сначала
      // человек видит состояние операции.
      current.message=PAYMENT_STATUS_TEXT;
      return;
    }
    writeOperation({order_id:result.order_id,payment_url:result.payment_url||'',link_opened:true,finished:false,saved_at:Date.now(),user_id:api.user?.id||null});
    await openPaymentWindow(result.payment_url,method,current);
  }
  async function applyPrepared(result,id,method,current){
    if(result?.state==='pending'&&typeof result.payment_url==='string'&&result.payment_url){
      await openCheckout(result,method,current);
      if(result.order_id)getRouter().openDetail({name:'purchase-order',id:result.order_id});
      return;
    }
    if(result?.state==='pending'){
      current.message='Счёт создан и отправлен в чат с ботом: оплатите его там.';
      current.failed=true;current.retry=false;
      if(result.order_id){rememberOrder(result.order_id);getRouter().openDetail({name:'purchase-order',id:result.order_id});}
      return;
    }
    current.message=paymentState(result);
    current.failed=true;
    current.retry=false;
    if(result?.order_id){rememberOrder(result.order_id);getRouter().openDetail({name:'purchase-order',id:result.order_id});}
  }
  async function applyPrepareFailure(cause,id,method,current){
    const data=cause instanceof ApiError?cause.data:null;
    const orderId=data&&typeof data.order_id==='string'&&ORDER_ID.test(data.order_id)?data.order_id:'';
    const failure=describeFailure(cause,{context:'prepare'});
    if(orderId){
      // Счёт мог быть создан: второй заказ не создаём, показываем состояние первого.
      rememberOrder(orderId);
      current.message=PAYMENT_STATUS_TEXT;current.failed=false;current.retry=false;
      getRouter().openDetail({name:'purchase-order',id:orderId});
      await loadOrder(orderId,{fresh:true});
      return;
    }
    if(failure.pending){
      // Пока идёт восстановление, человек видит, что мы проверяем платёж,
      // а не «нет связи»: ответ мог не дойти, но заказ уже создан.
      current.message=PAYMENT_STATUS_TEXT;current.failed=false;current.retry=false;refresh();
      if(await recover(id,method,current))return;
    }
    current.message=failure.message;current.failed=true;current.retry=failure.retry;
  }
  async function recover(id,method,current){
    // Ответ не дошёл: повторяем тот же запрос с тем же ключом. Сервер идемпотентен
    // по ключу, поэтому второй заказ не появится — вернётся уже созданный.
    if(disposed||!current.requestKey)return false;
    const request=new AbortController(), timer=setTimeout(()=>request.abort(),15000);requests.add(request);
    try{
      const result=await api.post('/app/api/purchase/prepare',{product:id,method,request_key:current.requestKey},{signal:request.signal});
      clearTimeout(timer);
      await applyPrepared(result,id,method,current);
      return true;
    }catch(cause){
      const data=cause instanceof ApiError?cause.data:null;
      const orderId=data&&typeof data.order_id==='string'&&ORDER_ID.test(data.order_id)?data.order_id:'';
      if(orderId){
        rememberOrder(orderId);
        current.message=PAYMENT_STATUS_TEXT;current.failed=false;current.retry=false;
        getRouter().openDetail({name:'purchase-order',id:orderId});
        await loadOrder(orderId,{fresh:true});
        return true;
      }
      const failure=describeFailure(cause,{context:'prepare'});
      current.message=failure.message;current.failed=true;current.retry=failure.retry;
      return false;
    }finally{clearTimeout(timer);requests.delete(request);}
  }
  async function choose(id, method) {
    const current=purchase(id);if(current.busy||disposed)return;
    const saved=operation();
    if(saved&&saved.product===id&&saved.method===method&&saved.order_id&&saved.link_opened&&!saved.finished){
      // Окно оплаты по этой операции уже открывалось: сначала состояние операции.
      current.busy=true;current.method=method;current.failed=false;current.retry=false;
      current.message=PAYMENT_STATUS_TEXT;refresh();
      getRouter().openDetail({name:'purchase-order',id:saved.order_id});
      await loadOrder(saved.order_id,{fresh:true});
      current.busy=false;refresh();return;
    }
    const reuse=saved&&saved.product===id&&saved.method===method&&!saved.finished?saved:null;
    current.busy=true;current.method=method;current.message='';current.failed=false;current.retry=false;
    current.requestKey=reuse?.request_key||current.requestKey||crypto.randomUUID();
    writeOperation({
      request_key:current.requestKey,product:id,method,user_id:api.user?.id||null,
      order_id:reuse?.order_id||'',payment_url:reuse?.payment_url||'',
      link_opened:Boolean(reuse?.link_opened),finished:false,saved_at:Date.now(),
    });
    refresh();
    const request=new AbortController(), timer=setTimeout(()=>request.abort(),20000);requests.add(request);
    try{
      const result=await api.post('/app/api/purchase/prepare',{product:id,method,request_key:current.requestKey},{signal:request.signal});
      clearTimeout(timer);
      await applyPrepared(result,id,method,current);
    }catch(cause){
      await applyPrepareFailure(cause,id,method,current);
    }finally{clearTimeout(timer);requests.delete(request);current.busy=false;refresh();}
  }
  async function loadOrder(id,{fresh=false}={}) {
    const current=orders.get(id)||{loading:false,value:null,error:'',retry:false,token:0,controller:null};orders.set(id,current);
    if(disposed||(current.loading&&!fresh))return;
    current.controller?.abort();current.loading=true;
    const token=++current.token;
    const request=new AbortController(), timer=setTimeout(()=>request.abort(),5000);requests.add(request);
    current.controller=request;
    try{
      const value=await api.post('/app/api/purchase/state',{order_id:id},{signal:request.signal});
      if(current.token===token&&!disposed){
        current.value=value;current.error='';current.retry=false;
        const saved=operation();
        if(saved&&saved.order_id===id){
          const finished=value.status!=='pending'||['confirmed','refunded','canceled'].includes(value.financial_status);
          if(finished)clearOperation();
          else writeOperation({expires_at:value.checkout_expires_at||saved.expires_at||null,finished:false});
        }
      }
    }catch(cause){
      if(current.token===token&&!disposed){
        const failure=describeFailure(cause,{context:'order'});
        current.error=failure.message;current.retry=failure.retry;
        if(cause instanceof ApiError&&(cause.status===403||cause.status===404)){
          const saved=operation();if(saved&&saved.order_id===id)clearOperation();
        }
      }
    }
    finally{clearTimeout(timer);requests.delete(request);if(current.token===token&&!disposed){current.loading=false;current.controller=null;refresh();}}
  }
  async function resumePayment(orderId){
    // Осознанное действие человека на экране операции: состояние уже показано,
    // поэтому открыть оплату здесь — не «повторное открытие вслепую».
    const saved=operation();
    if(!saved||saved.order_id!==orderId||disposed)return;
    const current=purchase(saved.product);
    current.busy=true;current.failed=false;current.retry=false;refresh();
    try{
      if(saved.payment_url){
        await openPaymentWindow(saved.payment_url,saved.method,current);
        return;
      }
      // Ссылки нет: повторяем подготовку тем же ключом — второй заказ не создастся.
      current.method=saved.method;current.message=PAYMENT_STATUS_TEXT;refresh();
      await recover(saved.product,saved.method,current);
    }finally{current.busy=false;refresh();}
  }
  function renderOrder(target,id) {
    target.append(element('h1','','Моя операция'));
    if(!orders.has(id))void loadOrder(id);
    const current=orders.get(id);
    if(current.loading&&current.value){const note=element('p','notice','Обновляем статус…');note.setAttribute('role','status');target.append(note);}
    if(current.error){target.append(panel('Операция недоступна',current.error));if(current.retry)target.append(action('Повторить',()=>loadOrder(id)));return;}
    if(!current.value){target.append(panel('Проверяем статус','Загружаем вашу операцию.'));return;}
    const order=current.value, offer=catalog.products.find(p=>p.product_id===order.product);
    target.append(element('h2','',offer?.title||'Plus'),element('p','',orderStatus(order)));
    if(order.monetary)target.append(element('p','muted',catalog.methods.find(m=>m.id===order.method)?.title||'Способ оплаты'));
    target.append(element('p','muted',`Создана ${dateText(order.created_at)}`));
    if(order.access_expires_at)target.append(element('p','muted',`Срок доступа по операции: до ${dateText(order.access_expires_at)}`));
    target.append(element('p','',order.effective_access.viewer?'Возможности Зритель Plus сейчас активны.':'Возможности Зритель Plus сейчас не активны.'));
    const update=action('Обновить статус',()=>loadOrder(id),true);update.disabled=current.loading;target.append(update);
    const saved=operation();
    const waiting=order.status==='pending'&&!['confirmed','refunded','canceled'].includes(order.financial_status);
    if(waiting&&saved&&saved.order_id===id)target.append(action(saved.payment_url?'Открыть оплату снова':'Проверить и открыть оплату',()=>resumePayment(id),true));
    target.append(navigationRow('Документы и поддержка','','help',()=>getRouter().openDetail('support')));
  }
  const visibility=()=>{const route=getRouter().state;if(!document.hidden&&route.detail?.name==='purchase-order')void loadOrder(route.detail.id,{fresh:true});};
  document.addEventListener('visibilitychange',visibility);
  return {
    refresh:load,
    refreshOrder:id=>loadOrder(id,{fresh:true}),
    restoreOperation(){
      // E3: после перезагрузки (в том числе из-за истёкшей подписи Telegram)
      // человек возвращается на экран последней операции, а не на «нет связи».
      const saved=operation();
      if(!saved||saved.finished||!saved.order_id)return false;
      const now=Date.now();
      if(saved.saved_at&&now-saved.saved_at>OPERATION_TTL)return false;
      if(saved.expires_at&&now>saved.expires_at*1000+OPERATION_TTL)return false;
      requested=true;void load();
      getRouter().openDetail({name:'purchase-order',id:saved.order_id});
      return true;
    },
    render(target,route) {
      target.replaceChildren();if(!requested)void load();
      if(!catalog){target.append(element('h1','','Как оплатить?'),panel('Способы оплаты',error||'Загружаем тариф и цену.'));if(error&&catalogRetry)target.append(action('Повторить',load));return;}
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
      if(current.message){const note=element('p',current.failed?'notice error':'notice',current.message);note.setAttribute('role',current.failed?'alert':'status');target.append(note);if(current.retry)target.append(action('Повторить',()=>choose(id,current.method),true));}
      target.append(navigationRow('Документы и поддержка','','help',()=>getRouter().openDetail('support')));
    },
    dispose(){disposed=true;++generation;controller?.abort();for(const request of requests)request.abort();document.removeEventListener('visibilitychange',visibility);},
  };
}
