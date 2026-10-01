import {Series, useVideoConfig} from 'remotion';
import {OpeningScene, PostScene, PreviewScene, ViewerScene} from './Scenes';

export const DemoVideo = () => {
  const {fps} = useVideoConfig();
  return (
    <Series>
      <Series.Sequence name="Эфир" durationInFrames={60} premountFor={fps}><OpeningScene /></Series.Sequence>
      <Series.Sequence name="Пост" durationInFrames={60} premountFor={fps}><PostScene /></Series.Sequence>
      <Series.Sequence name="Превью" durationInFrames={60} premountFor={fps}><PreviewScene /></Series.Sequence>
      <Series.Sequence name="Зритель" durationInFrames={60} premountFor={fps}><ViewerScene /></Series.Sequence>
    </Series>
  );
};
