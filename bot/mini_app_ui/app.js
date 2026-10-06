import { createApi, ApiError } from './api.js';
import { createTelegramAdapter } from './telegram.js';
import { createRouter } from './router.js';
import { element, action, icon, dialog, navigationRow, closeActiveDialog } from './components.js';
import { createViewerFeature } from './viewer.js';
import { createStreamerFeature } from './streamer.js';
import { createSubscriptionFeature } from './subscription.js';
import { createPurchaseFeature } from './purchase.js';
import { createThemeController } from './theme.js';
import { createProfileFeature } from './profile.js';
import { createSupportFeature } from './support.js';
import { createReportsFeature } from './reports.js';

const content = document.getElementById('content');
const modeSwitch = document.getElementById('mode-switch');
const tabBar = document.getElementById('tab-bar');
let session = null;
let authError = null;
let canRetryEntry = false;
let bootstrapPending = false;
let bootstrapController = null;
let disposed = false;
let renderContext = null;
let router;
let viewerFeature;
let streamerFeature;
let subscriptionFeature;
let purchaseFeature;
let profileFeature;
let supportFeature;
let reportsFeature;
let subscriptionOpen = false;
let purchaseOrderOpen=null;
let historyOpen=false;
const telegram = createTelegramAdapter(() => { if(!closeActiveDialog())router.back(); });
let themeStorage;
try { themeStorage = window.localStorage; } catch {}
export const theme = createThemeController({storage:themeStorage,telegram});
const AUTH_RELOAD_FLAG='ts-app-auth-reloaded';
function handleAuthExpired(){
  // Подпись Telegram живёт ограниченное время. Перезагружаем приложение один раз,
  // чтобы клиент выдал свежую; если и это не помогло, объясняем человеку словами.
  let already=false;
  try{already=window.sessionStorage.getItem(AUTH_RELOAD_FLAG)==='1';}catch{}
  if(!already){
    try{window.sessionStorage.setItem(AUTH_RELOAD_FLAG,'1');}catch{}
    window.location.reload();
    return;
  }
  authError='Сессия истекла. Закройте приложение и откройте его заново из чата с ботом.';
  canRetryEntry=true;
  router.refresh();
}
const api = createApi(telegram.initData,{onAuthExpired:handleAuthExpired});
router = createRouter(render);
const onDialogChange=()=>telegram.syncBack(Boolean(document.querySelector('dialog[open]'))||router.canBack);
document.addEventListener('app-dialog-change',onDialogChange);
const onAppMenu=event=>{
  dialog('Меню приложения',(box,close)=>{
    const roles=element('p','role-explanation','Зритель: уведомления для себя. Стример: публикации в свой Telegram-канал.');box.append(roles);
    for(const [label,glyph,callback] of [['Профиль','profile',()=>router.setTab('profile')],['Тариф','plus',()=>router.openDetail('subscription')],['Поддержка','help',()=>router.openDetail('support')]]){
      box.append(navigationRow(label,'',glyph,()=>{close();callback();}));
    }
  },{origin:event.currentTarget});
};
// Возврат связи: обновляем вход и данные, чтобы человек не видел устаревший экран.
function handleOnline(){
  if(disposed)return;
  if(!session){void bootstrap();return;}
  void viewerFeature?.refresh();void streamerFeature?.refresh();void profileFeature?.refresh();
  void subscriptionFeature?.refresh();router.refresh();
}
function handleVisible(){
  if(disposed||document.hidden||!session)return;
  void subscriptionFeature?.refresh();
}
function teardown(){
  window.removeEventListener('online',handleOnline);
  document.removeEventListener('visibilitychange',handleVisible);
  document.removeEventListener('app-dialog-change',onDialogChange);
  document.getElementById('app-menu').removeEventListener('click',onAppMenu);
  resizeNavigation.disconnect();
  theme.dispose();telegram.dispose();router.dispose();
  viewerFeature?.dispose();streamerFeature?.dispose();profileFeature?.dispose();supportFeature?.dispose();
  reportsFeature?.dispose();subscriptionFeature?.dispose();purchaseFeature?.dispose();
}
window.addEventListener('pagehide', (event) => {
  if(event.persisted)return;
  disposed = true; bootstrapController?.abort();
  teardown();
});
const resizeNavigation=new ResizeObserver(()=>document.documentElement.style.setProperty('--navigation-height',`${tabBar.getBoundingClientRect().height}px`));
resizeNavigation.observe(tabBar);
document.getElementById('app-menu').addEventListener('click',onAppMenu);
document.getElementById('app-menu').append(icon('more'));

function render(state, canBack) {
  if(disposed)return;
  const context=JSON.stringify([state.mode,state.tab,state.detail]);
  if(context!==renderContext){streamerFeature?.clearFeedback();renderContext=context;}
  modeSwitch.replaceChildren();
  for (const [mode, label] of [['viewer', 'Зритель'], ['streamer', 'Стример']]) {
    const button = element('button', '', label);
    button.type = 'button';
    button.dataset.focusKey = `mode:${mode}`;
    button.setAttribute('aria-pressed', String(mode === state.mode));
    button.setAttribute('aria-description',mode==='viewer'?'Уведомления о стримерах в личном чате':'Публикации в вашем Telegram-канале');
    button.addEventListener('click', () => router.setMode(mode));
    modeSwitch.append(button);
  }
  tabBar.replaceChildren();
  const tabs = state.mode === 'viewer'
    ? [['home', 'Главная', 'home'], ['streamers', 'Стримеры', 'people'], ['profile', 'Профиль', 'profile'], ['plus','Тариф','plus']]
    : [['channel', 'Мой канал', 'channel'], ['posts', 'Посты', 'posts'], ['profile', 'Профиль', 'profile'], ['plus','Тариф','plus']];
  const detailName=typeof state.detail==='object'?state.detail?.name:state.detail;
  if(detailName!=='reports')reportsFeature?.deactivate();
  if(detailName==='history'&&!historyOpen)viewerFeature?.resetHistory();
  historyOpen=detailName==='history';
  const plusActive=['subscription','purchase','purchase-order'].includes(detailName);
  for (const [id, label, glyph] of tabs) {
    const button = element('button', '', '');
    button.type = 'button';
    button.setAttribute('aria-label', label);
    button.dataset.focusKey=`tab:${id}`;
    if ((plusActive?'plus':state.tab) === id) button.setAttribute('aria-current', 'page');
    button.append(icon(glyph), element('span', '', label));
    button.addEventListener('click', () => router.setTab(id));
    tabBar.append(button);
  }
  telegram.syncBack(canBack||Boolean(document.querySelector('dialog[open]')));
  content.replaceChildren();
  if (authError) {
    content.append(element('h1', '', canRetryEntry ? 'Не удалось загрузить приложение' : 'Откройте приложение из Telegram'));
    content.append(element('p', 'lead', authError));
    if(canRetryEntry)content.append(action('Повторить вход',bootstrap));
    return;
  }
  if (!session) {
    const status=element('div', 'status-panel', 'Проверяем вход…');status.setAttribute('role','status');content.append(status);
    return;
  }
  if(detailName!=='purchase-order')purchaseOrderOpen=null;
  if (detailName === 'subscription') {
    if (!subscriptionOpen) void subscriptionFeature.refresh();
    subscriptionOpen = true;
    subscriptionFeature.render(content, state);
    return;
  }
  subscriptionOpen = false;
  if(detailName==='purchase'||detailName==='purchase-order'){
    if(detailName==='purchase-order'&&purchaseOrderOpen!==state.detail.id){purchaseOrderOpen=state.detail.id;void purchaseFeature.refreshOrder(state.detail.id);}
    purchaseFeature.render(content,state);return;
  }
  if(detailName==='support'||detailName==='legal'){supportFeature.render(content,state);return;}
  if(detailName==='reports'){reportsFeature.render(content,state);return;}
  if(detailName==='viewer-settings'){viewerFeature.render(content,{...state,tab:'profile',detail:null});return;}
  if(detailName==='history'){viewerFeature.render(content,{...state,detail:'history'});return;}
  if(!state.detail&&state.tab==='profile'){profileFeature.render(content,state);return;}
  const featureState=state.detail&&typeof state.detail==='object'?{...state,detail:state.detail.id}:state;
  if (state.mode === 'viewer') {
    viewerFeature.render(content, featureState);
    return;
  }
  streamerFeature.render(content, featureState);
}

async function bootstrap() {
  if(disposed||bootstrapPending||session||!telegram.initData)return;
  bootstrapPending=true;authError=null;canRetryEntry=false;
  const controller=new AbortController();bootstrapController=controller;
  // Первый запрос после холодного старта контейнера может не уложиться в пять
  // секунд: даём пятнадцать, чтобы человек не видел ложную ошибку связи.
  const timer=setTimeout(()=>controller.abort(),15000);
  router.refresh();
  try {
    const verified = await api.post('/app/api/bootstrap',{}, {signal:controller.signal});
    if(disposed)return;
    api.bindIdentity(verified.user);
    session=verified;
    try{window.sessionStorage.removeItem(AUTH_RELOAD_FLAG);}catch{}
    viewerFeature=createViewerFeature(api,()=>router,telegram);
    streamerFeature=createStreamerFeature(api,()=>router,telegram);
    profileFeature=createProfileFeature(api,()=>router,theme);
    supportFeature=createSupportFeature(api,()=>router,telegram);
    reportsFeature=createReportsFeature(api,()=>router);
    subscriptionFeature=createSubscriptionFeature(api,()=>router,()=>{void viewerFeature.refresh();void streamerFeature.refresh();void profileFeature.refresh();});
    purchaseFeature=createPurchaseFeature(api,()=>router,telegram);
    // Подпись Telegram могла истечь, пока человек был на оплате: приложение
    // перезагрузилось и обязано вернуть его на экран последней операции.
    if(new URLSearchParams(location.search).get('screen')==='subscription')router.openDetail('subscription');
    else purchaseFeature.restoreOperation();
  } catch (error) {
    if(disposed)return;
    canRetryEntry = !(error instanceof ApiError && (error.status === 401 || error.status === 403));
    authError = !canRetryEntry
      ? 'Время входа истекло. Откройте приложение заново из чата бота.'
      : 'Не удалось связаться с сервером. Проверьте интернет и попробуйте ещё раз.';
  } finally {
    clearTimeout(timer);bootstrapPending=false;bootstrapController=null;
    if(!disposed)router.refresh();
  }
}

if (!telegram.initData) {
  authError = 'Для входа нужна кнопка приложения в чате бота.';
  router.refresh();
} else await bootstrap();

// Возврат связи и возврат в приложение: обновляем вход и данные, чтобы человек
// не видел устаревший экран. Обработчики именованные — их снимает teardown().
window.addEventListener('online',handleOnline);
document.addEventListener('visibilitychange',handleVisible);
