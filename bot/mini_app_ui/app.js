import { createApi, ApiError } from './api.js';
import { createTelegramAdapter } from './telegram.js';
import { createRouter } from './router.js';
import { element, panel, icon, dialog, navigationRow, closeActiveDialog } from './components.js';
import { createViewerFeature } from './viewer.js';
import { createStreamerFeature } from './streamer.js';
import { createSubscriptionFeature } from './subscription.js';
import { createPurchaseFeature } from './purchase.js';
import { createThemeController } from './theme.js';
import { createProfileFeature } from './profile.js';
import { createSupportFeature } from './support.js';

const content = document.getElementById('content');
const modeSwitch = document.getElementById('mode-switch');
const tabBar = document.getElementById('tab-bar');
let session = null;
let authError = null;
let router;
let viewerFeature;
let streamerFeature;
let subscriptionFeature;
let purchaseFeature;
let profileFeature;
let supportFeature;
let subscriptionOpen = false;
let purchaseOrderOpen=null;
let historyOpen=false;
const telegram = createTelegramAdapter(() => { if(!closeActiveDialog())router.back(); });
let themeStorage;
try { themeStorage = window.localStorage; } catch {}
export const theme = createThemeController({storage:themeStorage,telegram});
const api = createApi(telegram.initData);
router = createRouter(render);
const onDialogChange=()=>telegram.syncBack(Boolean(document.querySelector('dialog[open]'))||router.canBack);
document.addEventListener('app-dialog-change',onDialogChange);
window.addEventListener('pagehide', (event) => {
  if(event.persisted)return;
  document.removeEventListener('app-dialog-change',onDialogChange);theme.dispose();telegram.dispose();router.dispose();viewerFeature?.dispose();streamerFeature?.dispose();profileFeature?.dispose();supportFeature?.dispose();subscriptionFeature?.dispose();purchaseFeature?.dispose();resizeNavigation.disconnect();
});
const resizeNavigation=new ResizeObserver(()=>document.documentElement.style.setProperty('--navigation-height',`${tabBar.getBoundingClientRect().height}px`));
resizeNavigation.observe(tabBar);
document.getElementById('app-menu').addEventListener('click',event=>{
  dialog('Меню приложения',(box,close)=>{
    for(const [label,glyph,callback] of [['Профиль','profile',()=>router.setTab('profile')],['Подписка','plus',()=>router.openDetail('subscription')],['Поддержка','help',()=>router.openDetail('support')]]){
      box.append(navigationRow(label,'',glyph,()=>{close();callback();}));
    }
  },{origin:event.currentTarget});
});
document.getElementById('app-menu').append(icon('more'));

function render(state, canBack) {
  modeSwitch.replaceChildren();
  for (const [mode, label] of [['viewer', 'Зритель'], ['streamer', 'Стример']]) {
    const button = element('button', '', label);
    button.type = 'button';
    button.dataset.focusKey = `mode:${mode}`;
    button.setAttribute('aria-pressed', String(mode === state.mode));
    button.addEventListener('click', () => router.setMode(mode));
    modeSwitch.append(button);
  }
  tabBar.replaceChildren();
  const tabs = state.mode === 'viewer'
    ? [['home', 'Главная', 'home'], ['streamers', 'Стримеры', 'people'], ['profile', 'Профиль', 'profile'], ['plus','Plus','plus']]
    : [['channel', 'Мой канал', 'channel'], ['posts', 'Посты', 'posts'], ['profile', 'Профиль', 'profile'], ['plus','Plus','plus']];
  const detailName=typeof state.detail==='object'?state.detail?.name:state.detail;
  if(detailName==='history'&&!historyOpen)viewerFeature.resetHistory();
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
    content.append(element('p', 'eyebrow', 'Вход'), element('h1', '', 'Откройте приложение из Telegram'));
    content.append(element('p', 'lead', authError));
    content.append(panel('Сохраните введённое', 'При повторном открытии вернитесь к нужному разделу. Данные на сервер не отправлены.'));
    return;
  }
  if (!session) {
    content.append(element('div', 'status-panel', 'Проверяем вход…'));
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

router.refresh();
if (!telegram.initData) {
  authError = 'Для входа нужна кнопка приложения в чате бота.';
  router.refresh();
} else {
  try {
    session = await api.post('/app/api/bootstrap');
    api.bindIdentity(session.user);
    viewerFeature=createViewerFeature(api,()=>router,telegram);
    streamerFeature=createStreamerFeature(api,()=>router,telegram);
    profileFeature=createProfileFeature(api,()=>router,theme);
    supportFeature=createSupportFeature(api,()=>router,telegram);
    subscriptionFeature=createSubscriptionFeature(api,()=>router,()=>{void viewerFeature.refresh();void streamerFeature.refresh();void profileFeature.refresh();});
    purchaseFeature=createPurchaseFeature(api,()=>router,telegram);
    if(new URLSearchParams(location.search).get('screen')==='subscription')router.openDetail('subscription');
  } catch (error) {
    authError = error instanceof ApiError && (error.status === 401 || error.status === 403)
      ? 'Время входа истекло. Откройте приложение заново из чата бота.'
      : 'Связь прервалась. Откройте приложение заново, когда сеть восстановится.';
  }
  router.refresh();
}
