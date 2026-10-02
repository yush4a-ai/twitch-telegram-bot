// Semantic tokens from the accepted A palette; Telegram is an explicit choice.
const LIGHT = Object.freeze({
  canvas:'#f5f6f8', surface:'#ffffff', text:'#20242b', secondary:'#626a78', line:'#dce0e6',
  accent:'#1762db', soft:'#edf3ff', danger:'#a82b36', link:'#1762db', header:'#f5f6f8',
  bottom:'#ffffff', 'button-bg':'#1762db', 'button-fg':'#ffffff', 'control-track':'#eceef2', warning:'#835600',
});
const DARK = Object.freeze({
  canvas:'#171717', surface:'#242424', text:'#f1f1f1', secondary:'#bababa', line:'#3d3d3d',
  accent:'#91baff', soft:'#333333', danger:'#ffa4af', link:'#91baff', header:'#171717',
  bottom:'#242424', 'button-bg':'#91baff', 'button-fg':'#141414', 'control-track':'#3d3d3d', warning:'#eac774',
});
const CHOICES = new Set(['light','dark','telegram']);
const KEY = 'ts-app-theme';
const validColor = value => typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value);
const color = (value, fallback) => validColor(value) ? value.toLowerCase() : fallback;

function luminance(hex) {
  const channels = [1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)/255).map(x=>x<=.04045 ? x/12.92 : ((x+.055)/1.055)**2.4);
  return channels[0]*.2126+channels[1]*.7152+channels[2]*.0722;
}
function contrast(a,b) { const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05); }
function readable(foreground,background) {
  if(contrast(foreground,background)>=4.5)return foreground;
  return contrast('#ffffff',background)>contrast('#171717',background)?'#ffffff':'#171717';
}
function telegramTokens(snapshot) {
  const base = snapshot?.colorScheme === 'dark' ? DARK : LIGHT;
  const p = snapshot?.themeParams || {};
  const tokens = {
    ...base, canvas:color(p.bg_color,base.canvas), surface:color(p.section_bg_color,color(p.secondary_bg_color,base.surface)),
    text:color(p.text_color,base.text), secondary:color(p.subtitle_text_color,color(p.hint_color,base.secondary)),
    accent:color(p.accent_text_color,color(p.button_color,base.accent)), link:color(p.link_color,base.link),
    header:color(p.header_bg_color,color(p.bg_color,base.header)), bottom:color(p.bottom_bar_bg_color,color(p.secondary_bg_color,base.bottom)),
    line:color(p.section_separator_color,base.line), danger:color(p.destructive_text_color,base.danger),
    'button-bg':color(p.button_color,base['button-bg']), 'button-fg':color(p.button_text_color,base['button-fg']),
  };
  for(const name of ['text','secondary','accent','link','danger']) {
    if(contrast(tokens[name],tokens.canvas)<4.5 || contrast(tokens[name],tokens.surface)<4.5) {
      const black=Math.min(contrast('#171717',tokens.canvas),contrast('#171717',tokens.surface));
      const white=Math.min(contrast('#ffffff',tokens.canvas),contrast('#ffffff',tokens.surface));
      tokens[name]=black>=white?'#171717':'#ffffff';
    }
  }
  tokens['button-fg']=readable(tokens['button-fg'],tokens['button-bg']);
  return tokens;
}

export function applyThemeTokens(tokens, {choice, colorScheme}) {
  const root=document.documentElement;
  for(const [name,value] of Object.entries(tokens))root.style.setProperty(`--${name}`,value);
  root.dataset.theme=colorScheme;
  root.dataset.themeChoice=choice;
  root.style.colorScheme=colorScheme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content',tokens.canvas);
  document.querySelector('meta[name="color-scheme"]')?.setAttribute('content',colorScheme);
}

export function createThemeController({storage, telegram, applyTokens=applyThemeTokens}) {
  let choice='light',disposed=false;
  try { const saved=storage?.getItem(KEY);if(CHOICES.has(saved))choice=saved; }catch{}
  function refresh() {
    if(disposed)return;
    const snapshot=telegram?.getTheme?.();
    const scheme=choice==='telegram' ? (snapshot?.colorScheme==='dark'?'dark':'light') : choice;
    const tokens=choice==='telegram'?telegramTokens(snapshot):{...(choice==='dark'?DARK:LIGHT)};
    applyTokens(tokens,{choice,colorScheme:scheme});
    telegram?.setColors?.(tokens);
  }
  const unsubscribe=telegram?.subscribeTheme?.(refresh);
  refresh();
  return {
    getChoice:()=>choice,
    setChoice(next) {
      if(!CHOICES.has(next))throw new TypeError('Unknown theme choice');
      if(disposed)return;
      choice=next;try{storage?.setItem(KEY,next);}catch{}
      refresh();
    },
    refresh,
    dispose(){if(disposed)return;disposed=true;unsubscribe?.();},
  };
}
