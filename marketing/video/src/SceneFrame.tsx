import {loadFont} from '@remotion/fonts';
import {AbsoluteFill, Img, staticFile, useVideoConfig} from 'remotion';
import type {ReactNode} from 'react';

export const colors = {
  bg: '#F7F9FC',
  paper: '#FFFFFF',
  ink: '#10192A',
  muted: '#52627A',
  blue: '#1765F5',
  pale: '#E8F0FF',
  line: '#DCE5F0',
};

void loadFont({
  family: 'Golos',
  url: staticFile('golos-cyrillic.woff2'),
  weight: '400 900',
  unicodeRange: 'U+0400-045F,U+0490-0491,U+04B0-04B1,U+2116',
});
void loadFont({
  family: 'Golos',
  url: staticFile('golos-latin.woff2'),
  weight: '400 900',
});

export const useLayout = () => {
  const {width, height} = useVideoConfig();
  const portrait = height > width;
  return {width, height, portrait, side: portrait ? 58 : 88, top: portrait ? 100 : 72};
};

export const SceneFrame = ({children, step}: {children: ReactNode; step: string}) => {
  const layout = useLayout();
  return (
    <AbsoluteFill style={{backgroundColor: colors.bg, color: colors.ink, fontFamily: 'Golos, Arial, sans-serif', overflow: 'hidden'}}>
      <div style={{position: 'absolute', left: layout.side, right: layout.side, top: layout.top, display: 'flex', alignItems: 'center', justifyContent: 'space-between', zIndex: 2}}>
        <Img src={staticFile('brand-logo.png')} style={{width: layout.portrait ? 220 : 235, height: 'auto', borderRadius: 12}} />
        <span style={{color: colors.blue, border: `2px solid ${colors.blue}`, borderRadius: 6, fontSize: layout.portrait ? 20 : 18, fontWeight: 800, letterSpacing: '0.12em', padding: '9px 12px'}}>ДЕМО</span>
      </div>
      {children}
      <div style={{position: 'absolute', bottom: layout.portrait ? 100 : 68, left: layout.side, right: layout.side, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 24, borderTop: `2px solid ${colors.line}`, paddingTop: 18, color: colors.muted, fontSize: layout.portrait ? 20 : 16, fontWeight: 700, letterSpacing: '0.08em'}}>
        <span>{step} / 04</span><span>ТЕСТОВЫЙ БОТ</span>
      </div>
    </AbsoluteFill>
  );
};
