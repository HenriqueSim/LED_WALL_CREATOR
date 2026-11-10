# LED Wall Creator

A desktop editor for programming LED wall backgrounds for live shows, the way you program drums
in a DAW. You build the visual elements of a song (a sunset sun, a horizontal beam, a hexagon,
vertical bars...), draw *when* each one lights up in a pattern editor, arrange those patterns over
the song's waveform, and render the result to an `.mp4` for the LED wall (3840×1080 by default).

![The pattern editor](docs/screenshot.png)

## How to use the editor

The window has a live **preview** on top (Space = play / pause, Home = back to the start) and six
tabs below. A typical song goes through them in this order:

1. **Look & Song: load the song.** *Load Audio File…* analyses the track and detects the BPM and
   where bar 1 starts. Fix them by hand if needed (½ / ×2 for half or double tempo, *Auto* to go
   back to the detected values). Pick **one colour for the song**: every element and every member
   share it. The *Pastel* swatches suit a soft, sunset look. The same tab holds the global light
   settings (glow, attack / release, audio reactivity, strobe rate, room for lyrics) and the
   **band name** layer: text, stacked "vespa. / maia" layout, colour (*Dark cut-out* turns it into
   a silhouette cut into whatever is behind it), size, and its own fade-in / fade-out and glow peak.

2. **Band: members and groups.** List the members from left to right as they stand on stage, and
   make groups (e.g. *Left side*, *Rhythm section*). Every member and every group gets a lane inside
   every element in the pattern editor.

3. **Elements: build what is on the wall.** *Add* a preset: **Sunset sun**, **Horizontal beam**,
   **Hexagon / Polygon**, **Ring**, **Single bar**, **Vertical blocks**, **Light shafts** or
   **Horizon lights**. Each type shows only its own settings, e.g. a hexagon's radius and sides,
   the number of bars, or a beam's segments and parallel lines. In the preview you can click an
   element to select it, drag it to move it and use the mouse wheel to resize it. While you're in
   this tab the preview pulses every element on the beat, so you can see the effect. The checkbox
   next to an element means *available in this project*. The top of the list is the front layer.

   Every element is made of **parts** (a hexagon's sides, the bars, a beam's segments), shared
   out between the members in order. With 6 members and a 6-segment beam, each member has one
   segment. A 1-segment beam or a single bar lights up when anyone in it plays.

4. **Pattern Editor: draw the light.** A pattern is a loop of 1–16 bars on a 1/16 grid. Every
   element is a folder of lanes:
   - **All**: the whole element
   - **one lane per member** and **one per group**: their parts of that element
   - **Pulse / Chase / Random / Strobe**: motion on that element

   An element is only on the wall while one of its lanes plays, so notes are how you bring
   elements **in and out of the scene**. The **Global** folder holds Sparkle, Flash Peak (an
   accent-coloured burst on whatever is in the scene), Blackout and Band Name.

   Note length = how long it's lit, note velocity = how bright (the *Velocity* box sets new notes
   and edits the selected ones). While you edit, playback **loops the section** of the master
   track you're working on.

   | Action | How |
   |---|---|
   | Draw a note | drag on an empty cell |
   | Move / resize | drag the note / drag its right edge |
   | Delete | right-click or double-click |
   | Select several | Shift-drag, Ctrl+A |
   | Copy / cut / paste / duplicate | Ctrl+C / X / V / D |
   | Nudge | arrow keys |
   | Zoom | Ctrl+wheel |
   | Fold a folder | click its row |
   | Move the playhead | click or drag the ruler (bar numbers) |

5. **Master Track: arrange the song.** Place patterns as clips on three tracks over the
   waveform: double-click a track (or *Place pattern at playhead*). Drag a clip to move it
   (Ctrl-drag copies). Drag its right edge to stretch it: the pattern repeats. **Double-click a
   clip to open its pattern in the Pattern Editor at the spot you clicked.** Right-click for
   duplicate, delete, *make unique* (its own copy of the pattern) and *reset length*. The
   **Lyrics** lane puts text in the band reserved at the top of the wall (double-click it to add a
   line). Shift-drag the ruler to set a **loop region**, and tick *Seamless loop* for a background
   that loops without a visible jump.

6. **Export: render the video.** Choose 3840×1080, 1920×1080 or a custom size, and 30 or 60 fps.
   The video covers the loop region if there is one, otherwise the whole song. With ffmpeg
   installed you get a high-bitrate H.264 file and can include the song's audio.

Save the project with **File → Save Project** (`.json`). Undo / redo is Ctrl+Z / Ctrl+Shift+Z.

## Install and run

Needs Python 3.10+. ffmpeg is optional but recommended (it reads more audio formats, gives a
better H.264 encode and can put the audio in the video).

```bash
cd python
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python led_wall_motion_generator.py            # or: ... my_song.json / ... my_song.wav
```

On Linux, install ffmpeg with `sudo apt install ffmpeg`; on macOS, `brew install ffmpeg`.

## Code layout

```
python/
  led_wall_motion_generator.py   launcher (fonts, theme, main window)
  fonts/                         Anton (band name font, SIL Open Font License)
  ledwall/
    model.py       song data: members, groups, elements, patterns, clips, lyrics, look
    audio.py       audio decoding + librosa analysis (BPM, beats, energy) in a thread
    engine.py      time -> frame state: evaluates clips / notes, envelopes, per-element levels
    renderer.py    frame state -> image: element drawing, bloom, band name, lyrics (OpenCV)
    export.py      video export thread (OpenCV VideoWriter / ffmpeg)
    controller.py  undo / redo and change notifications
    gui/           one module per tab + the main window
docs/reference/    design references (logo layout, vertical blocks)
```
