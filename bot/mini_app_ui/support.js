import {element,action,panel} from './components.js';

export function createSupportFeature(api,getRouter,telegram) {
  let state=null,loading=false,requested=false,error='',disposed=false;
  async function refresh(){
    if(loading||disposed)return;loading=true;requested=true;
    try{state=await api.post('/app/api/support');error='';}
    catch{error='Не удалось загрузить поддержку. Попробуйте ещё раз.';}
    finally{loading=false;if(!disposed)getRouter().refresh();}
  }
  return {
    refresh,
    render(target){
      target.replaceChildren();target.append(element('h1','','Поддержка'));if(!requested)void refresh();
      if(!state){target.append(panel('Связаться с поддержкой',error||'Загружаем контакт и документы…'));if(error)target.append(action('Повторить загрузку',refresh,true));return;}
      target.append(panel('Связаться с поддержкой',state.contact?.label||'Контакт поддержки пока не указан.'));
      if(state.contact?.url)target.append(action('Написать в поддержку',()=>telegram.openTelegramLink(state.contact.url)));
    },
    dispose(){disposed=true;},
  };
}
