import {Composition, Folder, Still} from 'remotion';
import {DemoVideo} from './DemoVideo';
import {OpeningScene, PostScene, Poster, PreviewScene, ViewerScene} from './Scenes';

export const RemotionRoot = () => (
  <>
    <Folder name="Scenes">
      <Composition id="Opening" component={OpeningScene} durationInFrames={60} fps={30} width={1280} height={720} />
      <Composition id="Post" component={PostScene} durationInFrames={60} fps={30} width={1280} height={720} />
      <Composition id="Preview" component={PreviewScene} durationInFrames={60} fps={30} width={1280} height={720} />
      <Composition id="Viewer" component={ViewerScene} durationInFrames={60} fps={30} width={1280} height={720} />
    </Folder>
    <Composition id="JourneyLandscape" component={DemoVideo} durationInFrames={240} fps={30} width={1280} height={720} />
    <Composition id="JourneyPortrait" component={DemoVideo} durationInFrames={240} fps={30} width={720} height={1280} />
    <Still id="JourneyPoster" component={Poster} width={1280} height={720} />
  </>
);
