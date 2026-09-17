# badminton-reels

Turn a long badminton recording into rally highlights, cut for Instagram Reels.

Point your phone at the court, play for an hour, drop the file in, and the
editor finds each rally, ranks them, and renders vertical clips with the
action kept in frame. There is a browser editor for the cutting and a CLI for
everything scriptable — both work on the same project files.

It is built for the footage described below — iPhone 15 Pro Max, held
horizontally, 0.5x — but nothing in it is specific to that phone.

```bash
pip install -e .        # needs python 3.9+, numpy and ffmpeg
bdr serve               # opens the editor at http://127.0.0.1:8765
```

## The editor

`bdr serve` runs a small local web app and opens it. Nothing leaves the
machine: it binds to loopback only, and every API call needs the token in the
URL it prints, so another page in your browser cannot drive it.

Drop a video on the start screen, press **Analyse**, and you land in the
cutting room:

* **Player** with the planned 9:16 crop drawn over the footage, so you can see
  what will survive the crop before rendering anything.
* **Reel preview** beside it: the actual vertical composition, composited live
  from the video as it plays. What you see is what the file will contain — the
  crop path comes from the same planner the renderer uses, not an
  approximation of it.
* **Two-tier timeline** — the whole match up top, a zoomable detail track
  below, both drawn over the activity curve with the racket impacts ticked in.
  Drag a clip edge to trim, drag its middle to slide it, drag empty space to
  add a rally the detector missed, `b` to split at the playhead.
* **Rally inspector** for the in/out points, keep/drop, renaming, and
  per-rally overrides of layout, zoom, pan and speed.
* **Framing and Render tabs** for the project defaults, then *Render kept
  clips* or *Render reel*, with progress and the finished files listed for
  preview and download.
* **Detect tab** re-runs rally detection with different thresholds. Motion and
  impacts are already measured and stored, so this takes a fraction of a
  second — no decoding again.

Every edit saves itself to `project.json` a moment later; the pill in the
header tells you when.

```
~/badminton-reels/            the workspace (bdr serve -w elsewhere)
  media/                      uploads, and anything you point --media at
  projects/<id>/
    project.json              the timeline; the CLI reads the same file
    proxy.mp4                 small H.264 copy the browser scrubs
    thumbs/                   cached rally thumbnails
    out/                      rendered clips and reels
```

Keyboard: `space` play, `j`/`k` rally to rally, `i`/`o` set in/out at the
playhead, `[` `]` `-` `=` nudge by 0.1 s, `,`/`.` step a frame, `x` drop, `s`
keep, `n` add, `b` split, `+`/`−`/`0` zoom the timeline. `?` lists them.

The preview proxy is H.264, so the editor wants a browser with an H.264
decoder — Chrome, Safari, Edge and Firefox all qualify.

## Why the framing matters

A 9:16 window cut out of a 16:9 frame keeps **32% of the width**. At 0.5x the
whole court fits, which also means the players are small, so a centre crop
throws away most of the rally. Three layouts deal with that differently:

| Layout | What you get | Use it when |
|---|---|---|
| `follow` *(default)* | A 9:16 window the full height of the frame, panned to keep the action centred. Most pixels per player. | The phone is behind the baseline, so the rally runs away from the camera. |
| `stack` | Whole court across the top, a tracked close-up filling the rest. | The phone is at the side of the court and the rally crosses the frame. |
| `fit` | Whole court, scaled to the canvas width, floating on a blurred copy of itself. | You want context over detail, or the camera moved during the rally. |

The pan is planned from the analysis pass, not tracked live, so it is smoothed
without lag: a median filter removes shuttle-sized jitter, a zero-phase
exponential filter removes the wobble, and a speed limit prevents whip pans.
Set pan to *hold still* for a fixed crop at the middle of the rally's action.

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
  from footage that still cuts to 30 fps for Reels.
* **Don't mute the phone.** Racket impacts are the single most reliable rally
  signal, and the shot count is what ranks a rally as a highlight.

### HDR

iPhones record HLG HDR by default. Converting that to H.264 without tone
mapping is what makes exported clips look washed out and grey. HDR sources are
detected and tone mapped through linear light automatically — in the render and
in the preview proxy, so the browser shows the right colours too. This needs an
ffmpeg built with `zscale` (Homebrew's is); otherwise you get a warning and an
approximate conversion.

## Output

Every clip is written for Reels: 1080x1920, H.264 high profile, yuv420p,
bt709-tagged, 30 fps, 2-second keyframes, AAC stereo 48 kHz, `faststart`.
Silent sources still get an audio track, because Reels rejects clips without
one. A reel keeps the total under 90 seconds by default.

## The command line

The CLI covers the same engine for scripting and batches. `bdr <command>
--help` has the detail.

```bash
bdr info match.mov                      # what ffprobe sees, and how much a 9:16 crop keeps
bdr auto match.mov -o out               # one-shot: detect, clips, reel
bdr analyze match.mov -o p.json         # detect rallies only
bdr list p.json                         # the timeline as a table
bdr edit p.json --drop r04,r07 --renumber
bdr edit p.json --trim r03=+0.5,-0.3    # 0.5s earlier in, 0.3s earlier out
bdr edit p.json --set r05=layout:stack --set r05=speed:0.5
bdr render p.json -o clips --layout stack --labels
bdr reel p.json -o reel.mp4 --top 8
bdr render p.json -o slow --speed 0.5 --only r03
bdr render p.json -o out --music bed.m4a --music-volume 0.25
bdr render p.json -o out --size 1080x1350          # feed post, 4:5
```

`project.json` is plain JSON and safe to edit by hand. Per-clip `layout`,
`pan`, `zoom` and `speed` override the project defaults, so one rally can be
slow motion in a different framing without affecting the rest.

The editor keeps its projects in its own workspace, so to open a project the
CLI made, move it to `~/badminton-reels/projects/<name>/project.json`. It is
the same format either way.

## When detection gets it wrong

The editor prints what it found, and the Detect tab re-runs detection in place.
From the CLI the same knobs are flags:

| Symptom | Try |
|---|---|
| Rallies missing entirely | sensitivity 1.5, fewest impacts 1, start level 0.35 |
| Warm-up and knock-ups detected as rallies | fewest impacts 4, shortest rally 4 s |
| Two rallies joined into one | quiet-before-end 0.8, join gaps 0.8 |
| One rally split in two | quiet-before-end 2.0, join gaps 2.5 |
| Serves cut off at the start | lead-in 1.5 s |
| The winning shot cut off | tail 2.0 s |
| The next court's game gets picked up | `--region 10%,20%,80%,70%` |
| Crowd noise firing the impact detector | sensitivity 0.7 |
| No audio in the file | expected: it falls back to motion only, so lower the start level |
| Pan swings too much | pan smoothing 1.2, max pan speed 0.35, centre pull 0.4 |
| Players drift out of the crop | zoom 0.85 (wider, letterboxed) or the `stack` layout |

`--region x,y,w,h` (fractions or percentages) restricts analysis to part of
the frame. Without it, the busy area is estimated from accumulated motion,
which on a static shot is the court; the Detect tab shows the estimate.

## How it works

1. **Motion.** One decode pass at ~192px wide, 12 samples a second, measures
   how much the frame changes and where. The accumulated change over the whole
   file gives the court region for free, with outlier trimming so a passer-by
   cannot stretch it. The same pass writes the preview proxy, so the browser
   gets a scrubable copy for the price of its encode.
2. **Impacts.** A band-limited spectral flux onset detector (1.2–7.5 kHz)
   finds racket hits. A candidate has to clear an adaptive local threshold, a
   floor tied to the loudest transients in the file, and a loudness gate —
   any one alone fires on an empty hall's own hiss.
3. **Fusion.** Both signals are normalised per file, mixed 60/40, and cut into
   rallies with hysteresis: enter high, leave low, and hang on for 1.2 s of
   quiet before calling a rally over. Segments shorter than 2 s or with fewer
   than 2 impacts are dropped; anything over 45 s is split at its quietest
   interior moment.
4. **Ranking.** `0.45 × shots + 0.30 × length + 0.25 × intensity`. Shot count
   leads because a long exchange is what makes a highlight. Clips you trim or
   split by hand are re-scored the same way.
5. **Framing and render.** The crop path is planned in Python and handed to
   ffmpeg as a `sendcmd` script driving a `crop` filter, so the pan costs
   nothing at render time and the clip is encoded in one pass. The editor asks
   the same planner for the path it draws on screen.

Analysis is the only slow step, and it runs once: everything after it reads
the stored signals.

## Tests

```bash
python -m unittest discover -s tests -t .
```

The unit tests are pure NumPy and cover the detector, the framing maths, the
timeline model and the filtergraphs. The API tests boot a real server on a
free port and drive it over HTTP, including path-traversal and token checks.
The end-to-end tests build a synthetic match with ffmpeg, check detection
against the scripted rally times, then render and probe real MP4s; they skip
themselves if ffmpeg is missing.

```bash
python tests/make_fixture.py /tmp/match.mp4   # the synthetic match, if you want to poke at it
```

## Limitations

* Rallies are found from movement and impacts, not from the rules of the game.
  Scores, lets and service faults are invisible to it — that is what the
  editor is for.
* A moving camera (handheld, panning) weakens both the court estimate and the
  crop path. It will still cut rallies.
* On a busy multi-court hall, impacts from the next court count as impacts.
  `--region` fixes the motion side of that; the audio side cannot be
  separated.
* Analysis decodes the whole file, so a 4K HEVC hour takes a while. Try
  `--hwaccel videotoolbox` on a Mac.
* One job runs at a time. ffmpeg already uses every core, so overlapping two
  renders only makes both slower.
