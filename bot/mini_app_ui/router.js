const TABS={viewer:['home','streamers','profile','plus'],streamer:['channel','posts','profile','plus']};
const detailKey=detail=>detail&&typeof detail==='object'?`${detail.name}:${detail.id||''}`:detail||'';
const focusKey=node=>node?.dataset?.focusKey||node?.id||(node?.getAttribute?.('aria-label')||node?.textContent?.trim())?.slice(0,512);

export function createRouter(onChange) {
  let savedMode='viewer';try{savedMode=localStorage.getItem('ts-app-mode')==='streamer'?'streamer':'viewer';}catch{}
  let state={mode:savedMode,tab:TABS[savedMode][0],detail:null},generation=0;
  const stack=[],positions=new Map(),key=item=>`${item.mode}:${item.tab}:${detailKey(item.detail)}`;
  let activation=null;
  const onPointer=event=>{activation={node:event.target.closest?.('button,a,input,select,textarea'),at:performance.now()};};
  document.addEventListener('pointerdown',onPointer,true);
  function capture({navigation=false}={}){
    const active=navigation&&activation?.node?.isConnected&&performance.now()-activation.at<1500?activation.node:document.activeElement;
    const rows=[...document.querySelectorAll('#content [data-row-key], #content .list-row')];
    const anchor=rows.find(node=>{const r=node.getBoundingClientRect();return r.bottom>0&&r.top<innerHeight;});
    return {scroll:window.scrollY,focus:active!==document.body?focusKey(active):null,anchor:anchor?.dataset.rowKey||anchor?.textContent,top:anchor?.getBoundingClientRect().top};
  }
  function restore(position,{navigation=false}={}){
    const token=++generation,activeAtSchedule=document.activeElement;
    requestAnimationFrame(()=>{
      if(token!==generation)return;
      const snapshot=position||{scroll:0};window.scrollTo(0,snapshot.scroll);
      if(snapshot.anchor){const anchor=[...document.querySelectorAll('#content [data-row-key], #content .list-row')].find(node=>(node.dataset.rowKey||node.textContent)===snapshot.anchor);if(anchor)window.scrollBy(0,anchor.getBoundingClientRect().top-snapshot.top);}
      const active=document.activeElement;
      // WebKit leaves click focus on the persistent main while its children change.
      // Preserve any new user focus, but allow restoration past that container.
      if(active!==document.body&&active?.isConnected&&!(active===activeAtSchedule&&active===document.getElementById('content')))return;
      const focused=snapshot.focus&&[...document.querySelectorAll('button,input,select,textarea,a,[tabindex]')].find(node=>focusKey(node)===snapshot.focus);
      if(focused)focused.focus({preventScroll:true});
      else if(navigation){const heading=document.querySelector('#content h1');if(heading){heading.tabIndex=-1;heading.focus({preventScroll:true});}}
    });
  }
  function navigate(next,push=true){
    positions.set(key(state),capture({navigation:true}));activation=null;if(positions.size>512)positions.delete(positions.keys().next().value);
    if(push){stack.push(state);if(stack.length>100)stack.shift();}
    state=next;onChange(state,stack.length>0);restore(positions.get(key(state)),{navigation:true});
  }
  return {
    get state(){return state;},
    get canBack(){return stack.length>0;},
    setMode(mode){
      if(!TABS[mode]||mode===state.mode)return;try{localStorage.setItem('ts-app-mode',mode);}catch{}
      const subscription=detailKey(state.detail)==='subscription'||state.tab==='plus';
      navigate({mode,tab:subscription?'plus':TABS[mode][0],detail:subscription?'subscription':null});
    },
    setTab(tab){
      if(!TABS[state.mode].includes(tab))return;
      if(tab==='plus'){if(detailKey(state.detail)==='subscription')return;navigate({...state,tab,detail:'subscription'});return;}
      if(tab===state.tab&&!state.detail)return;navigate({...state,tab,detail:null});
    },
    openDetail(detail){
      if(typeof detail==='object'&&detail){if(typeof detail.name!=='string'||detail.name.length>64||typeof detail.id!=='string'||detail.id.length>256)throw new TypeError('Invalid detail route');detail=Object.freeze({name:detail.name,id:detail.id});}
      else if(typeof detail!=='string'||!detail||detail.length>256)throw new TypeError('Invalid detail route');
      if(detailKey(detail)===detailKey(state.detail))return;navigate({...state,detail});
    },
    back(){if(!stack.length)return false;navigate(stack.pop(),false);return true;},
    tabs(mode=state.mode){return TABS[mode];},
    refresh(){const position=capture();onChange(state,stack.length>0);restore(position);},
    dispose(){document.removeEventListener('pointerdown',onPointer,true);++generation;},
  };
}
