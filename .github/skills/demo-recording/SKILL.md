---
name: demo-recording
description: Record, inspect, trim and package the fictional Javorna Teams demo; preserve originals, truthfulness and image quality.
---

# Record from the scenario, not the old footage

Use `scenario/demo.json` and the generated HTML guide. Record only the new cast,
country units and Javorna interface. Do not import any earlier video or screenshot.
Keep new raw MP4s in ignored `video-raw/`, named after scene IDs.

Before recording, verify the operator's identity and the fresh scenario state.
Never send a message as a human unless specifically authorized. Login and
credentials are not footage. Use separate profiles for the human roles.

Inspect the full relevant clip and its timing before writing an EDL. Keep:
the submitted prompt, meaningful progress, the eyes reaction when present,
the fully rendered output and final acknowledgement. Remove long idle tails.
Speed up waits only with a visible time-compression badge. Never splice a
different result so that it implies a tool action that did not happen.

# Edit

Use the repository's `video-production/build_clips.py` and `edl.json`.
Validate with `--check` first. Record real source bounds in seconds, not guessed
timings. Use `speed`, `badge` and `hold` only where the inspected footage warrants.
Raw files are read-only; encoded outputs go to ignored `docs/video/`.
Use original resolution, 30 fps, H.264 CRF 16, yuv420p, faststart; no audio unless
the presenter requests it. No quality reduction in the presentation copy.

After every cut: probe duration/resolution, watch boundaries and the final
frames, then verify actual playback. Confirm that no credentials or unrelated
messages remain. Screenshots must come from the new recording and carry no
invented evidence. Do not claim planned screenshots are already available.

# Package

The current deck deliberately contains recording placeholders, not a video
runtime. Integrate actual clips only after the first new recording is approved.
Keep the e-mail re-encode and full-quality presentation export distinct.
Never overwrite raw material or describe an encoded H.264 copy as lossless.
Keep an operator recording log tied to scene IDs.
