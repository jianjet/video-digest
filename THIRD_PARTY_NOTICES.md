# Third-party notices

## bradautomates/claude-video (MIT)
https://github.com/bradautomates/claude-video

Adapted from `skills/watch/scripts/frames.py` at commit `03ceb42`, in `skills/video-digest/scripts/media_digest.py`:
`pick_sync`, `showinfo_times`, `scene_frames`, `thumbnails`, `dedupe`, `cue_frames` (scene detection, the 16x16
near-duplicate check and cue frames). Each is marked with a comment in the code.

```
MIT License

Copyright (c) 2026 Bradley Bonanno

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## devinilabs/claude-watch (MIT)
https://github.com/devinilabs/claude-watch

Idea only: Claude looks at key frames of a video, not only its transcript. No code from it is included.

## Programs run, not bundled
These are installed separately (`requirements.txt`, SETUP.md) and called as separate programs or libraries; none of
their code is in this repo.
- gallery-dl (GPL-2.0), yt-dlp (Unlicense), ffmpeg (LGPL/GPL builds)
- sherpa-onnx (Apache-2.0) with the SenseVoice and Silero VAD models, downloaded at first run
- RapidOCR (Apache-2.0), onnxruntime (MIT)
