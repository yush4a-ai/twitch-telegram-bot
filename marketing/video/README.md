# R8 synthetic demo video

This isolated Remotion project renders an eight-second explanation of the test bot journey. The footage is synthetic. It is **not** the 24-second Telegram live-preview pipeline and contains no Twitch capture, user data, official platform logos, payment claims, or audio.

## Local workflow

From `marketing/video`, run `npm ci`, then `npm run dev` to inspect both `JourneyLandscape` and `JourneyPortrait` in Studio. Check frames 30, 90, 150, and 210 at both aspect ratios. `npm run lint` runs ESLint and TypeScript checks.

The checked-in site assets were rendered with:

```powershell
npx remotion render JourneyLandscape ../../bot/growth_ui/demo-landscape.mp4 --codec h264 --crf 22 --concurrency 2
npx remotion render JourneyPortrait ../../bot/growth_ui/demo-portrait.mp4 --codec h264 --crf 22 --concurrency 2
npx remotion still JourneyLandscape ../../bot/growth_ui/demo-poster.png --frame 30
```

Run `python -m scripts.verify_r8_video` from the repository root to validate dimensions, duration, video-only streams, and asset sizes with `ffprobe`. The site uses native video controls and does not preload or autoplay the video.

All Remotion packages are pinned to `4.0.530`. The official `4.0.531` CLI archive fetched on 2026-10-01 contained an empty `dist/render-queue/queue.js` and Studio failed to start; `4.0.530` passed Studio, lint, and local renders. Do not run the template's upgrade command without repeating that check.

Remotion licensing depends on organization and use. Local evaluation does not establish permission for later public or commercial distribution. See the [license terms](https://www.remotion.dev/docs/license/pricing) before any public release. No cloud rendering or purchase was used here.
