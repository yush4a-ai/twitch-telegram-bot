import {interpolate, useCurrentFrame} from 'remotion';
import {colors, SceneFrame, useLayout} from './SceneFrame';

const appearance = (frame: number) => interpolate(frame, [0, 15, 50, 59], [0, 1, 1, 0], {
  extrapolateLeft: 'clamp',
  extrapolateRight: 'clamp',
});

const Heading = ({kicker, title, detail}: {kicker: string; title: string; detail: string}) => {
  const {portrait} = useLayout();
  return (
    <div style={{maxWidth: portrait ? 600 : 620}}>
      <div style={{color: colors.blue, fontSize: portrait ? 22 : 18, fontWeight: 800, letterSpacing: '0.12em', marginBottom: 18}}>{kicker}</div>
      <div style={{fontSize: portrait ? 62 : 70, fontWeight: 850, letterSpacing: '-0.055em', lineHeight: 1.03}}>{title}</div>
      <div style={{color: colors.muted, fontSize: portrait ? 28 : 26, lineHeight: 1.36, marginTop: 22}}>{detail}</div>
    </div>
  );
};

const SignalVisual = ({frame, size}: {frame: number; size: number}) => {
  const growth = interpolate(frame, [0, 35], [0.8, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <div style={{position: 'relative', width: size, height: size, flexShrink: 0}}>
      {[1, 0.72, 0.44].map((ratio, index) => (
        <div key={ratio} style={{
          position: 'absolute', left: '50%', top: '50%', width: size * ratio, height: size * ratio,
          marginLeft: -size * ratio / 2, marginTop: -size * ratio / 2, borderRadius: '50%',
          border: `3px solid ${colors.blue}`, opacity: (0.17 + index * 0.16) * appearance(frame), scale: growth,
        }} />
      ))}
      <div style={{
        position: 'absolute', left: '50%', top: '50%', width: size * 0.16, height: size * 0.16,
        marginLeft: -size * 0.08, marginTop: -size * 0.08, borderRadius: '50%',
        backgroundColor: colors.blue, boxShadow: '0 0 50px #1765F58A', opacity: appearance(frame),
      }} />
    </div>
  );
};

export const OpeningScene = () => {
  const frame = useCurrentFrame();
  const l = useLayout();
  return (
    <SceneFrame step="01">
      <div style={{position: 'absolute', opacity: appearance(frame), top: l.portrait ? 260 : 155, bottom: l.portrait ? 225 : 120, left: l.side, right: l.side, display: 'flex', flexDirection: l.portrait ? 'column' : 'row', alignItems: 'center', justifyContent: 'space-between', gap: l.portrait ? 70 : 75}}>
        <SignalVisual frame={frame} size={l.portrait ? 370 : 390} />
        <Heading kicker="01 / НАЧАЛО" title="Эфир начался" detail="Подключённый канал выходит в эфир." />
      </div>
    </SceneFrame>
  );
};

const DemoPost = () => {
  const {portrait} = useLayout();
  return (
    <div style={{width: portrait ? 555 : 510, border: `2px solid ${colors.line}`, backgroundColor: colors.paper, borderRadius: 20, boxShadow: '0 22px 48px #10192A16', padding: 32}}>
      <div style={{display: 'flex', alignItems: 'center', gap: 15}}>
        <div style={{width: 44, height: 44, borderRadius: '50%', backgroundColor: colors.blue}} />
        <strong style={{fontSize: portrait ? 26 : 24}}>Стример в эфире</strong>
        <span style={{marginLeft: 'auto', color: colors.blue, backgroundColor: colors.pale, borderRadius: 5, padding: '5px 8px', fontSize: 14, fontWeight: 800}}>ДЕМО</span>
      </div>
      <div style={{color: colors.muted, fontSize: portrait ? 24 : 22, lineHeight: 1.35, marginTop: 24}}>Играет и общается с чатом. Загляните на трансляцию.</div>
      <div style={{color: colors.blue, fontSize: 22, fontWeight: 800, marginTop: 22}}>Смотреть эфир ↗</div>
    </div>
  );
};

export const PostScene = () => {
  const frame = useCurrentFrame();
  const l = useLayout();
  const rise = interpolate(frame, [0, 30], [35, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <SceneFrame step="02">
      <div style={{position: 'absolute', opacity: appearance(frame), top: l.portrait ? 280 : 170, bottom: l.portrait ? 215 : 120, left: l.side, right: l.side, display: 'flex', flexDirection: l.portrait ? 'column' : 'row', alignItems: 'center', justifyContent: 'space-between', gap: 40}}>
        <Heading kicker="02 / ПУБЛИКАЦИЯ" title="Live-пост в сообществе" detail="Бот готовит сообщение для подключённого Telegram-чата." />
        <div style={{translate: `0 ${rise}px`}}><DemoPost /></div>
      </div>
    </SceneFrame>
  );
};

const PreviewIllustration = () => {
  const {portrait} = useLayout();
  return (
    <div style={{width: portrait ? 560 : 520, aspectRatio: '16 / 9', backgroundColor: colors.ink, borderRadius: 22, padding: 22, boxShadow: '0 24px 52px #10192A26', position: 'relative', flexShrink: 0}}>
      <div style={{position: 'absolute', inset: 22, borderRadius: 14, background: 'linear-gradient(135deg,#16233A 0%,#25395F 60%,#1765F5 160%)'}} />
      <div style={{position: 'absolute', left: 78, bottom: 52, width: 175, height: 130, borderRadius: '80px 80px 14px 14px', backgroundColor: '#46658F', opacity: 0.7}} />
      <div style={{position: 'absolute', left: '50%', top: '50%', translate: '-50% -50%', width: 84, height: 84, borderRadius: '50%', backgroundColor: colors.paper, color: colors.blue, fontSize: 44, display: 'grid', placeItems: 'center'}}>▶</div>
      <span style={{position: 'absolute', right: 42, top: 42, color: colors.ink, backgroundColor: colors.paper, borderRadius: 6, padding: '8px 10px', fontSize: 16, fontWeight: 800}}>ПРЕВЬЮ · ДЕМО</span>
    </div>
  );
};

export const PreviewScene = () => {
  const frame = useCurrentFrame();
  const l = useLayout();
  return (
    <SceneFrame step="03">
      <div style={{position: 'absolute', opacity: appearance(frame), top: l.portrait ? 295 : 175, bottom: l.portrait ? 215 : 120, left: l.side, right: l.side, display: 'flex', flexDirection: l.portrait ? 'column' : 'row', alignItems: 'center', justifyContent: 'space-between', gap: 45}}>
        <PreviewIllustration />
        <Heading kicker="03 / ДОПОЛНЕНИЕ" title="Короткое превью" detail="Если источник доступен, пост может получить видео." />
      </div>
    </SceneFrame>
  );
};

const ViewerIcon = () => <div style={{position: 'relative', width: 220, height: 220}}>
  <div style={{position: 'absolute', left: 73, top: 12, width: 74, height: 74, borderRadius: '50%', backgroundColor: colors.blue}} />
  <div style={{position: 'absolute', left: 20, bottom: 9, width: 180, height: 122, borderRadius: '110px 110px 18px 18px', backgroundColor: colors.blue}} />
</div>;

export const ViewerScene = () => {
  const frame = useCurrentFrame();
  const l = useLayout();
  return (
    <SceneFrame step="04">
      <div style={{position: 'absolute', opacity: appearance(frame), top: l.portrait ? 280 : 175, bottom: l.portrait ? 215 : 120, left: l.side, right: l.side, display: 'flex', flexDirection: l.portrait ? 'column' : 'row', alignItems: 'center', justifyContent: 'space-between', gap: l.portrait ? 60 : 50}}>
        <div>
          <Heading kicker="04 / ЗРИТЕЛЬ" title="Пост и личный сигнал" detail="Участник сообщества видит пост. Личный подписчик получает отдельное сообщение." />
          <div style={{color: colors.paper, backgroundColor: colors.blue, borderRadius: 10, padding: '15px 22px', width: 'fit-content', fontSize: l.portrait ? 24 : 22, fontWeight: 800, marginTop: 30}}>Открыть тестовый бот ↗</div>
        </div>
        <div style={{width: l.portrait ? 350 : 310, height: l.portrait ? 350 : 310, borderRadius: '50%', border: `2px solid ${colors.line}`, backgroundColor: colors.pale, display: 'grid', placeItems: 'center', flexShrink: 0}}><ViewerIcon /></div>
      </div>
    </SceneFrame>
  );
};

export const Poster = () => {
  const l = useLayout();
  return (
    <SceneFrame step="00">
      <div style={{position: 'absolute', top: 180, bottom: 135, left: l.side, right: l.side, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 50}}>
        <Heading kicker="ИЗ ЭФИРА В TELEGRAM" title="Эфир → пост → зритель" detail="Синтетический пример работы тестового бота." />
        <SignalVisual frame={30} size={300} />
      </div>
    </SceneFrame>
  );
};
