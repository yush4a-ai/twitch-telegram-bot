export function element(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
}

export function panel(title, description) {
  const node = element('section', 'panel empty');
  node.append(element('strong', '', title), element('p', '', description));
  return node;
}

export function avatar(name, url) {
  const node=element('span','avatar',String(name||'?').slice(0,1).toUpperCase());node.setAttribute('aria-hidden','true');
  const valid=typeof url==='string'&&(/^https:\/\/static-cdn\.jtvnw\.net\/jtv_user_pictures\/[^?#]+$/.test(url)||/^https:\/\/t\.me\/i\/userpic\/[A-Za-z0-9_./=-]+$/.test(url)||/^data:image\/(?:jpeg|png);base64,[A-Za-z0-9+/=]+$/.test(url));
  if(valid){const image=element('img','avatar-image');image.alt='';image.width=44;image.height=44;image.loading='lazy';image.decoding='async';image.referrerPolicy='no-referrer';image.addEventListener('error',()=>image.remove(),{once:true});image.src=url;node.append(image);}
  return node;
}

export function action(label, callback, secondary = false) {
  const button = element('button', `button${secondary ? ' secondary' : ''}`, label);
  button.type = 'button';
  button.addEventListener('click', (event) => {
    if (button.getAttribute('aria-disabled') === 'true') return;
    const result = callback(event);
    if (result && typeof result.then === 'function') {
      button.setAttribute('aria-disabled', 'true');
      const finish = () => button.removeAttribute('aria-disabled');
      result.then(finish, () => {
        finish();
        if (!button.isConnected) return;
        const note = element('p','notice error','Не удалось выполнить действие. Попробуйте ещё раз.');
        note.setAttribute('role','alert');button.after(note);
      });
    }
  });
  return button;
}

const paths = {
  star:['m12 3 2.8 5.7 6.3.9-4.5 4.4 1 6.2-5.6-2.9-5.6 2.9 1-6.2-4.5-4.4 6.3-.9z'],
  home:['M3 11 12 3l9 8','M5 10v11h5v-7h4v7h5V10'],
  people:['M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2','M9 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8','M17 4a4 4 0 0 1 0 7','M22 21v-2a4 4 0 0 0-3-3.9'],
  profile:['M20 21v-2a8 8 0 0 0-16 0v2','M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8'],
  plus:['M12 3v18','M3 12h18'],channel:['M4 5h16v12H4z','M8 21h8','M12 17v4'],
  posts:['M5 3h14v18H5z','M8 7h8','M8 11h8','M8 15h5'],
  notification:['M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9','M10 21h4'],
  theme:['M21 12a9 9 0 1 1-9-9 7 7 0 0 0 9 9'],
  help:['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18','M9.5 9a2.5 2.5 0 0 1 5 0c0 2-2.5 2-2.5 4','M12 17h.01'],
  more:['M5 12h.01','M12 12h.01','M19 12h.01'],close:['M6 6l12 12','M6 18 18 6'],chevron:['m9 5 7 7-7 7'],
  video:['M3 5h13v14H3z','m16 9 5-3v12l-5-3'],folder:['M3 7V4h7l2 3h9v13H3z'],
  history:['M3 11a9 9 0 1 1 2 7','M3 4v7h7','M12 7v5l3 2'],chart:['M4 3v18h17','M8 16v-5','M13 16V7','M18 16V4'],
  text:['M4 5h16','M4 10h16','M4 15h10','M4 20h12'],buttons:['M3 5h18v6H3z','M3 15h8v6H3z','M15 15h6v6h-6z'],
  filter:['M3 4h18l-7 8v7l-4 2v-9z'],clock:['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18','M12 7v5l3 2'],
};

export function icon(name) {
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');svg.setAttribute('focusable','false');
  for(const d of paths[name]||paths.chevron){const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',d);svg.append(path);}
  return svg;
}

let dialogSequence=0;
const activeDialogs=[];
export function closeActiveDialog(){const close=activeDialogs.at(-1);if(!close)return false;close();return true;}
export function dialog(title,buildContent,{sheet=false,origin=document.activeElement,onClose}={}) {
  const overlay=element('dialog',`app-dialog${sheet?' app-sheet':''}`);
  const label=element('h2','',title);label.id=`app-dialog-title-${++dialogSequence}`;overlay.setAttribute('aria-labelledby',label.id);
  const header=element('div','dialog-header'),content=element('div','dialog-content');
  let closed=false;const previousOverflow=document.documentElement.style.overflow;
  function close(){if(closed)return;closed=true;overlay.close();overlay.remove();const index=activeDialogs.indexOf(close);if(index>=0)activeDialogs.splice(index,1);document.documentElement.style.overflow=previousOverflow;if(origin?.isConnected)origin.focus({preventScroll:true});onClose?.();document.dispatchEvent(new Event('app-dialog-change'));}
  const dismiss=action('Закрыть',close,true);dismiss.className='icon-button';dismiss.replaceChildren(icon('close'));dismiss.setAttribute('aria-label','Закрыть');
  header.append(label,dismiss);overlay.append(header,content);buildContent(content,close);
  overlay.addEventListener('cancel',event=>{event.preventDefault();close();});
  overlay.addEventListener('keydown',event=>{
    if(event.key==='Escape'){event.preventDefault();close();return;}
    if(event.key!=='Tab')return;
    const focusable=[...overlay.querySelectorAll('button,a[href],input,select,textarea,[tabindex]')]
      .filter(node=>node.tabIndex>=0&&!node.disabled&&node.getClientRects().length&&!node.closest('[inert]'));
    const first=focusable[0],last=focusable.at(-1),active=document.activeElement;
    if(first&&((event.shiftKey&&active===first)||(!event.shiftKey&&active===last))){
      event.preventDefault();(event.shiftKey?last:first).focus();
    }
  },true);
  overlay.addEventListener('click',event=>{const r=overlay.getBoundingClientRect();if(event.target===overlay&&(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom))close();});
  document.body.append(overlay);document.documentElement.style.overflow='hidden';overlay.showModal();activeDialogs.push(close);document.dispatchEvent(new Event('app-dialog-change'));
  return {element:overlay,close};
}

export function sheet(title,buildContent){return dialog(title,buildContent,{sheet:true});}

export function navigationRow(label,detail,glyph,callback,key=label) {
  const button=action(label,callback,true);button.className='navigation-row';button.setAttribute('aria-label',label);button.dataset.focusKey=`row:${key}`;
  const copy=element('span','row-copy');copy.append(element('span','row-title',label));if(detail)copy.append(element('small','muted',detail));
  button.replaceChildren(icon(glyph),copy,icon('chevron'));return button;
}
