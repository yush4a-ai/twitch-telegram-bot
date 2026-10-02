/* Shared DESIGN catalog. Not a server entitlement or live purchase configuration. */
(()=>{
 const features={
  tracked:{label:'Отслеживаемые стримеры',free:'До 50 бесплатно',plus:'До 200 с Plus'},
  photo:{label:'Уведомления с фото',free:'Бесплатно',plus:'Фото остаётся доступным'},
  video:{label:'Пять видеопревью',free:'Фото вместо видео',plus:'5 выбранных стримеров, включая тех, кто не в эфире'},
  filters:{label:'Фильтры по игре и словам',free:'Обычные уведомления',plus:'Например: только Minecraft или слово «турнир»'},
  category:{label:'Нужная категория',free:'Уведомление о начале эфира',plus:'Сообщение при переходе в выбранную категорию'},
  reminders:{label:'Напоминания',free:'Без повторного сообщения',plus:'Через 15 или 30 минут после начала эфира'},
  folders:{label:'Папки с правилами',free:'Общий список',plus:'Например: «Вечером» с общими фильтрами'},
  history:{label:'История событий',free:'Текущие уведомления',plus:'Собственные исходы уведомлений'},
  postVideo:{label:'Превью в посте',free:'Стандартный автоматический пост с фото',plus:'Видеопревью эфира; фото при временной перегрузке'},
  postStyle:{label:'Текст и оформление',free:'Стандартное сообщение',plus:'Свой текст и оформление публикации'},
  postButtons:{label:'Дополнительные кнопки',free:'Стандартная ссылка на эфир',plus:'Кнопки для разрешённых размещений'},
  postPresets:{label:'Сохранённые варианты',free:'Стандартный пост',plus:'Сохранять и выбирать оформление'},
 };
 const viewer=['tracked','photo','video','filters','category','reminders','folders','history'];
 const streamer=['postVideo','postStyle','postButtons','postPresets'];
 const catalog={kind:'design-fixture',paymentEnabled:false,autoRenew:false,features,plans:{
  free:{id:'free',name:'Free',mockRubles:null,starsPrice:null,trackedLimit:50,videoLimit:0,features:['tracked','photo']},
  viewer:{id:'viewer',name:'Viewer Plus',mockRubles:150,starsPrice:null,trackedLimit:200,videoLimit:5,features:viewer},
  streamer:{id:'streamer',name:'Streamer Plus',mockRubles:200,starsPrice:null,trackedLimit:200,videoLimit:5,features:[...viewer,...streamer],publicationFeatures:streamer,includes:'viewer'},
 }};
 function freeze(object){Object.values(object).forEach(value=>{if(value&&typeof value==='object')freeze(value);});return Object.freeze(object);}
 window.plusCatalog=freeze(catalog);
})();
