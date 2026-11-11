# Website

Live at **https://henriquesim.github.io/LED_WALL_CREATOR/**

The LED Wall Creator website: plain HTML / CSS / JS, no build step. Published to GitHub Pages by
`.github/workflows/pages.yml` on every push to `main` that changes this folder.

- `index.html` home (LED wall hero, scrolling feature showcase), `download.html`, `story.html`
- `assets/` styles, script, font (Inter), logo
- `media/` the demo videos, posters and app screenshots (all made with the app itself)
- `tools/` how the media is made (not published):
  - `make_demo.py "<song.mp3>"` builds the 10-bar demo project (`demo.ledwall`) and renders
    `media/wall.mp4` (with audio), `stage-led.mp4`, `stage-projector.mp4` and posters;
    `--sheet` only writes a contact sheet to check the look
  - `make_shots.py` app screenshots with the demo loaded (`media/ui-*.jpg`)

Run both from the `python/` folder with the app's environment, e.g.
`.venv/bin/python ../website/tools/make_demo.py "../song.mp3"`.

The download buttons link to `https://github.com/HenriqueSim/LED_WALL_CREATOR/releases/latest/download/<file>`,
which starts the newest release's file directly. The release file names must stay
`LEDWallCreator-windows.zip`, `LEDWallCreator-macos.zip` and `LEDWallCreator-linux.tar.gz`.

Preview locally: `cd website && python3 -m http.server 8000`, then open http://localhost:8000.
Add `?shot` to the address for full-page screenshots (no animations).
