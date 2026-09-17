# badminton-reels

Turn a long badminton recording into rally highlights, cut for Instagram Reels.

Point your phone at the court, play for an hour, and drop the file in. The tool
finds each rally, ranks them, gives you a page to review and re-cut them by
hand, and renders vertical 1080x1920 clips with the action kept in frame.

It is built for the footage described below — iPhone 15 Pro Max, held
horizontally, 0.5x — but nothing in it is specific to that phone.

```
bdr auto match.mov -o out        # detect, review page, one clip per rally, plus a reel
```

## Why the framing matters

A 9:16 window cut out of a 16:9 frame keeps **32% of the width**. At 0.5x the
whole court fits, which also means the players are small, so a centre crop
throws away most of the rally. Three layouts deal with that differently:

| `--layout` | What you get | Use it when |
|---|---|---|
| `follow` *(default)* | A 9:16 window the full height of the frame, panned to keep the action centred. Most pixels per player. | The phone is behind the baseline, so the rally runs away from the camera. |
| `stack` | Whole court across the top, a tracked close-up filling the rest. | The phone is at the side of the court and the rally crosses the frame. |
| `fit` | Whole court, scaled to the canvas width, floating on a blurred copy of itself. | You want context over detail, or the camera moved during the rally. |

The pan is planned from the analysis pass, not tracked live, so it is smoothed
without lag: a median filter removes shuttle-sized jitter, a zero-phase
exponential filter removes the wobble, and a speed limit prevents whip pans.
`--pan none` holds a fixed crop at the middle of the rally's action instead.

## Install

Needs Python 3.9+, `ffmpeg`/`ffprobe` on `PATH`, and NumPy.

```bash
brew install ffmpeg            # macOS;  Debian/Ubuntu: sudo apt install ffmpeg
pip install -e .               # from this directory
bdr info match.mov             # check it can read your footage
```

`bdr info` prints the size, frame rate, whether the file is HDR, and how much
width a 9:16 crop will keep. If you have no footage to hand:

```bash
python tests/make_fixture.py /tmp/match.mp4   # a synthetic match with 4 rallies
```

## The workflow

`bdr auto` runs everything, but the four steps are separate on purpose: the
analysis pass is the slow one, and once it has written `project.json` you can
re-cut and re-render as often as you like without touching the video again.

```bash
bdr analyze match.mov -o out/project.json --review   # find rallies (slow, once)
open out/project-review.html                         # watch, trim, drop
bdr render out/project.json -o out/clips             # one vertical clip per rally
bdr reel   out/project.json -o out/reel.mp4 --top 8  # the best ones, joined
```

### The review page

`bdr review` writes a single self-contained HTML file: thumbnails, an activity
sparkline per rally, and no network access. Open it, load the video file when
it asks, and:

* **Preview** plays just that rally, with a blue box showing what the vertical
  crop will keep.
* **Drop** removes a rally from the render — service errors, warm-up, the
  point where someone walked across the court.
* **in −/+**, **out −/+** nudge the cut points by 0.25s.
* `j`/`k` move, `space` previews, `x` drops, `s` keeps, `[`/`]` and `,`/`.`
  nudge the in and out points.

Then either **Download project.json** and replace the old file, or **Copy
render command** and paste the `--only r01,r04,…` line it built for you.

### Editing without the browser

```bash
bdr list out/project.json                       # the timeline as a table
bdr edit out/project.json --drop r04,r07        # drop two rallies
bdr edit out/project.json --top 8 --renumber    # keep the 8 best, relabel 1..8
bdr edit out/project.json --trim r03=+0.5,-0.3  # 0.5s earlier in, 0.3s earlier out
bdr edit out/project.json --set r05=layout:stack --set r05=speed:0.5
```

`project.json` is plain JSON and safe to edit by hand. Per-clip `layout`,
`pan`, `zoom` and `speed` override the project defaults, so one rally can be
slow motion in a different framing without affecting the rest.

## Filming for this

* **Horizontal, on something solid.** The detector assumes a fixed camera: it
  learns which part of the frame is the court and ignores the rest. A phone
  that gets bumped mid-match will still work, but the court estimate blurs.
* **Behind the baseline, as high as you can get it.** This is what makes
  `follow` work: the rally runs up and down the frame, which is the direction
  a 9:16 crop has room for.
* **0.5x is the ultra-wide camera.** It fits the whole court from close up,
  but it is a smaller, dimmer sensor with soft, stretched corners. Indoors,
  1x from further back gives visibly cleaner clips if you have the space.
  Either way, keep the play away from the very edges of the frame.
* **4K60 if you have the storage.** 60 fps gives clean half-speed slow motion
  (`--speed 0.5`) from footage that still cuts to 30 fps for Reels.
* **Don't mute the phone.** Racket impacts are the single most reliable rally
  signal, and the shot count is what ranks a rally as a highlight.

### HDR

iPhones record HLG HDR by default. Converting that to H.264 without tone
mapping is what makes exported clips look washed out and grey. HDR sources are
detected and tone mapped through linear light automatically; `--no-tonemap`
turns it off, and `--tonemap` forces it on for a file that is not tagged.
This needs an ffmpeg built with `zscale` (Homebrew's is) — otherwise you get a
warning and an approximate conversion.

## Output

Every clip is written for Reels: 1080x1920, H.264 high profile, yuv420p,
bt709-tagged, 30 fps, 2-second keyframes, AAC stereo 48 kHz, `faststart`.
Silent sources still get an audio track, because Reels rejects clips without
one. `bdr reel` keeps the total under 90 seconds by default (`--max-duration`).

```bash
bdr reel out/project.json -o reel.mp4 --top 6 --layout stack --labels
bdr render out/project.json -o slow --speed 0.5 --only r03      # half speed
bdr render out/project.json -o out --music bed.m4a --music-volume 0.25
bdr render out/project.json -o out --size 1080x1350             # feed post, 4:5
```

`--labels` burns a `Rally 3 · 14 shots` badge into the top safe area, above
where the Reels UI sits. It is on for reels and off for single clips.

## When detection gets it wrong

`bdr analyze` prints what it found. Compare it against the footage, then:

| Symptom | Try |
|---|---|
| Rallies missing entirely | `--sensitivity 1.5`, `--min-shots 1`, or `--enter 0.35` |
| Warm-up and knock-ups detected as rallies | `--min-shots 4`, `--min-rally 4` |
| Two rallies joined into one | `--hang 0.8 --merge-gap 0.8` |
| One rally split in two | `--hang 2.0 --merge-gap 2.5` |
| Serves cut off at the start | `--pre 1.5` |
| The winning shot cut off at the end | `--post 2.0` |
| The next court's game gets picked up | `--region 10%,20%,80%,70%` |
| Crowd noise firing the impact detector | `--sensitivity 0.7` |
| Nothing detected, and the file has no audio | expected: it falls back to motion only, so lower `--enter` |
| Pan swings too much | `--pan-tau 1.2`, `--pan-speed 0.35`, `--center-bias 0.4` |
| Players drift out of the crop | `--zoom 0.85` (wider, letterboxed) or `--layout stack` |

`--region x,y,w,h` (fractions or percentages) restricts analysis to part of
the frame. Without it, the busy area is estimated from accumulated motion,
which on a static shot is the court; `bdr analyze` prints the estimate.

## How it works

1. **Motion.** One decode pass at ~192px wide, 12 samples a second, measures
   how much the frame changes and where. The accumulated change over the whole
   file gives the court region for free, with outlier trimming so a passer-by
   cannot stretch it.
2. **Impacts.** A band-limited spectral flux onset detector (1.2–7.5 kHz)
   finds racket hits. A candidate has to clear an adaptive local threshold, a
   floor tied to the loudest transients in the file, and a loudness gate —
   any one alone fires on an empty hall's own hiss.
3. **Fusion.** Both signals are normalised per file, mixed 60/40, and cut into
   rallies with hysteresis: enter high, leave low, and hang on for 1.2s of
   quiet before calling a rally over. Segments shorter than 2s or with fewer
   than 2 impacts are dropped; anything over 45s is split at its quietest
   interior moment.
4. **Ranking.** `0.45 x shots + 0.30 x length + 0.25 x intensity`. Shot count
   leads because a long exchange is what makes a highlight.
5. **Framing and render.** The crop path is planned in Python and handed to
   ffmpeg as a `sendcmd` script driving a `crop` filter, so the pan costs
   nothing at render time and the clip is encoded in one pass.

## Tests

```bash
python -m unittest discover -s tests -t .
```

118 tests. The unit tests are pure NumPy; the end-to-end test builds the
synthetic match with ffmpeg, checks detection against the scripted rally
times, then renders and probes real MP4s. It skips itself if ffmpeg is
missing.

## Limitations

* Rallies are found from movement and impacts, not from the rules of the game.
  Scores, lets and service faults are invisible to it — that is what the
  review page is for.
* A moving camera (handheld, panning) weakens both the court estimate and the
  crop path. It will still cut rallies.
* On a busy multi-court hall, impacts from the next court count as impacts.
  `--region` fixes the motion side of that; the audio side cannot be
  separated.
* Analysis decodes the whole file, so a 4K HEVC hour takes a while. Try
  `--hwaccel videotoolbox` on a Mac.
