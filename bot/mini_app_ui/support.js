import {element,action,panel,navigationRow} from './components.js';

const titles={privacy:'Политика конфиденциальности',agreement:'Пользовательское соглашение',support:'Поддержка',tariffs:'Тарифы',payments:'Оплата'};

// Rebuild the server's escaped Markdown output with a small DOM allowlist.
function documentBody(html) {
  const target=element('article','legal-body');target.dataset.legalBody='';
  const parsed=new DOMParser().parseFromString(String(html||''),'text/html');
  function copy(node,parent){
    if(node.nodeType===Node.TEXT_NODE){parent.append(document.createTextNode(node.textContent));return;}
    if(node.nodeType!==Node.ELEMENT_NODE)return;
    const tag=node.tagName.toLowerCase();
    if(!['p','h2','h3','ul','li','strong','a'].includes(tag))return;
    const child=element(tag);
    if(tag==='a'){
      const href=node.getAttribute('href')||'';
      try{const url=new URL(href);const publicHost=/^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$/i.test(url.hostname)&&!/(?:^|\.)(?:local|localhost|internal|test|invalid)$/i.test(url.hostname)&&!/^(?:[0-9]+\.)*[0-9]+$/.test(url.hostname);if(!href.includes('\\')&&(url.protocol==='https:'&&!url.username&&!url.password&&!url.port&&publicHost||url.protocol==='mailto:'&&!url.search&&!url.hash)){
        child.href=href;child.rel='noopener noreferrer';
      }}catch{}
    }
    for(const nested of node.childNodes)copy(nested,child);
    parent.append(child);
  }
  for(const node of parsed.body.childNodes)copy(node,target);
  return target;
}

export function createSupportFeature(api,getRouter,telegram) {
  let state=null,loading=false,requested=false,error='',disposed=false,controller;
  const documents=new Map(),controllers=new Map();
  async function refresh(){
    if(loading||disposed)return;loading=true;requested=true;controller=new AbortController();
    const timer=setTimeout(()=>controller?.abort(),5000);
    try{state=await api.post('/app/api/support/state',{}, {signal:controller.signal});error='';}
    catch{error='Не удалось загрузить поддержку. Попробуйте ещё раз.';}
    finally{clearTimeout(timer);loading=false;if(!disposed)getRouter().refresh();}
  }
  async function loadDocument(id){
    if(!titles[id]||disposed)return;
    controllers.get(id)?.abort();const own=new AbortController();controllers.set(id,own);
    const previous=documents.get(id);documents.set(id,{...previous,loading:true,error:''});getRouter().refresh();
    const timer=setTimeout(()=>own.abort(),5000);
    try{
      const response=await fetch(`/app/legal/${id}?format=json`,{cache:'no-store',credentials:'same-origin',signal:own.signal});
      if(![200,503].includes(response.status))throw new Error('document unavailable');
      const doc=await response.json();
      if(doc.id!==id||typeof doc.body_html!=='string'||typeof doc.ready!=='boolean')throw new Error('invalid document');
      if(!disposed&&controllers.get(id)===own)documents.set(id,{document:doc,loading:false,error:''});
    }catch{
      if(!disposed&&controllers.get(id)===own)documents.set(id,{...previous,loading:false,error:'Не удалось загрузить документ. Попробуйте ещё раз.'});
    }finally{
      clearTimeout(timer);if(controllers.get(id)===own){controllers.delete(id);if(!disposed)getRouter().refresh();}
    }
  }
  return {
    refresh,
    render(target,routerState){
      target.replaceChildren();const detail=routerState?.detail;
      if(detail?.name==='legal'){
        const id=detail.id;if(!titles[id]){target.append(panel('Документ недоступен','Вернитесь к списку документов.'));return;}
        target.append(element('h1','',titles[id]));
        const value=documents.get(id);
        if(!value){void loadDocument(id);return;}
        target.setAttribute('aria-busy',String(value.loading));
        if(value.document){
          if(value.document.version)target.append(element('p','muted legal-version',`Редакция ${value.document.version}`));
          target.append(documentBody(value.document.body_html));
        }else target.append(element('p','muted',value.loading?'Загружаем документ…':value.error));
        if(value.error&&value.document)target.append(element('p','notice error',value.error));
        const reload=action('Обновить документ',()=>loadDocument(id),true);reload.disabled=value.loading;target.append(reload);return;
      }
      target.removeAttribute('aria-busy');target.append(element('h1','','Поддержка'));if(!requested)void refresh();
      if(!state){target.append(panel('Связаться с поддержкой',error||'Загружаем контакт и документы…'));if(error)target.append(action('Повторить загрузку',refresh,true));return;}
      if(state.available){
        target.append(element('p','lead','Поможем с подключением, уведомлениями и подпиской.'));
        if(state.telegram_url)target.append(action('Написать в Telegram',()=>telegram.openTelegramLink(state.telegram_url)));
        if(state.email){const link=element('a','button secondary',state.email);link.href=`mailto:${encodeURIComponent(state.email)}`;target.append(link);}
      }else target.append(panel('Связаться с поддержкой','Контакт поддержки пока не указан.'));
      target.append(element('h2','group-heading','Документы'));
      const list=element('div','profile-group');
      for(const doc of state.documents||[]){if(!titles[doc.id])continue;list.append(navigationRow(titles[doc.id],doc.ready?`Редакция ${doc.version}`:'Готовим актуальную редакцию','text',()=>getRouter().openDetail({name:'legal',id:doc.id}),`legal:${doc.id}`));}
      target.append(list);if(error)target.append(element('p','notice error',error));
      const reload=action('Обновить',refresh,true);reload.disabled=loading;target.append(reload);
    },
    dispose(){disposed=true;controller?.abort();for(const own of controllers.values())own.abort();controllers.clear();},
  };
}
