"""Generates the feature pages (website/features/<slug>.html) and the feature grid on the home page
from one list, so they stay in step.

    python3 website/tools/make_pages.py
"""

import html
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)

FEATURES = [
    dict(slug="pattern-editor", tile="Pattern grid", tile_text="Notes per element, member and group. Copy, paste, nudge, undo.",
         eyebrow="Pattern Editor", title="Draw light like you'd program a beat.",
         lead="Every shape on the wall has a folder of lanes: the whole shape, one lane per band member, "
              "one per group, and the motion effects. Draw notes on a 1/16 grid while the section loops.",
         video="rec-pattern", image="ui-pattern",
         steps=["Pick a pattern on the left (or click <b>New</b>). A pattern is a loop of 1 to 16 bars.",
                "Open an element's folder and drag across a lane to draw a note: its length is how long it's lit, "
                "the <b>Velocity</b> box sets how bright.",
                "Use the member and group lanes to light only part of a shape, for example the left side of the "
                "stage on the first beat and the right side on the second.",
                "Add <b>Pulse</b>, <b>Chase</b>, <b>Random</b> or <b>Strobe</b> for motion, and set "
                "<b>Effect speed</b> to ½× or 2× for half-time or double-time sections.",
                "Playback loops the part of the song you're editing, so you see the result straight away."],
         tips=["Shift-drag to select notes", "Ctrl+C / V / D", "Arrows nudge", "Click the ruler to jump",
               "Fold the folders you're not using"]),
    dict(slug="master-track", tile="Synced to audio", tile_text="Tempo detection, tap tempo, drag the waveform to line up bar 1.",
         eyebrow="Master Track", title="Locked to your song.",
         lead="Load your track and the tempo is found for you. Arrange your patterns along the song like "
              "clips in a music app, and every light lands on the beat.",
         video="rec-master", image="ui-master",
         steps=["Load your song in <b>Look &amp; Song</b> (WAV, MP3, M4A, FLAC…). The tempo and the first beat are detected.",
                "If the grid is slightly off, drag the waveform until bar 1 sits on the first beat, or use the "
                "<b>Bar 1 at</b> nudges and <b>Tap</b> at the bottom.",
                "Double-click a track to place the selected pattern; drag clips to move them and drag their right "
                "edge to repeat a pattern for longer.",
                "Shift-drag on the ruler to set a loop region, and tick <b>Seamless loop</b> for a clip that "
                "repeats without a jump.",
                "Double-click the Lyrics lane to add a line of lyrics at that moment."],
         tips=["½× / ×2 tempo buttons", "Three tracks for layering", "Ctrl-drag copies a clip",
               "Double-click a clip to edit its pattern"]),
    dict(slug="elements", tile="Ready-made shapes", tile_text="Sun, beam, hexagon, ring, blocks, shafts, horizon lights.",
         eyebrow="Elements", title="Simple shapes that breathe with the music.",
         lead="Pick the shapes for your wall and shape them with a few sliders. Each one only appears while "
              "its lanes play, so each section of the song can have its own look.",
         video="rec-elements", image="ui-elements",
         steps=["In <b>Elements</b>, click <b>Add</b> and pick a shape: a sunset sun, a horizontal beam, a hexagon, "
                "a ring, vertical blocks, light shafts or horizon lights.",
                "Change its size, position and look with the sliders. Each shape shows only the settings it needs, "
                "like the number of bars or a beam's segments.",
                "Drag it around in the preview and use the mouse wheel to resize it.",
                "Tick it in the list for each pattern that should use it. The ticks follow the pattern picked at the top.",
                "The preview pulses only the shape you're editing, so you see exactly what it does."],
         tips=["Top of the list = front", "Double-click a value to reset it", "One colour per song",
               "Each shape's parts are shared out between the members"]),
    dict(slug="custom-shapes", tile="Custom shapes", tile_text="Combine lines, dashes, arcs, rays and polygons into your own.",
         eyebrow="Custom elements", title="Build your own shapes.",
         lead="When the ready-made shapes aren't enough, combine lines, dashed lines, rectangles, circles, arcs, "
              "rays and polygons into one element. Each piece becomes a part the band can light.",
         video="rec-custom", image="rec-custom",
         steps=["In <b>Elements</b>, click <b>Add → Custom</b>.",
                "Use <b>Add shape</b> to add pieces: a line, dashes, a rectangle, a circle, an arc, rays or a polygon.",
                "Select a piece to change its size, angle, number of rays or dashes, or drag it in the preview.",
                "Scale, move, rotate or spin the whole element with the sliders at the top.",
                "Each piece is one part: in the Pattern Editor they're shared out between the members, in list order."],
         tips=["Copy a piece to repeat it", "Filled or outline", "Spin on the beat"]),
    dict(slug="glow-haze", tile="Glow & haze", tile_text="Bloom, flashes, blackouts and haze that follows the light.",
         eyebrow="Glow & haze", title="Light that fills the room.",
         lead="Every lit part can spread a soft haze into the air around it, so lighting one dash of a beam "
              "lights the room around that dash. A separate Haze element washes the stage with colour.",
         video="rec-haze", image="rec-haze",
         steps=["Select an element and raise <b>Haze around it</b> in its Light settings: one fader sets how far "
                "the haze spreads and how bright it is.",
                "<b>Glow</b> sets the bloom right around the shape.",
                "Add a <b>Haze</b> element for a soft wash of the song colour rising from the bottom. Put notes on its "
                "lane where you want it; the velocity sets how much.",
                "In the Global lanes, <b>Flash Peak</b> gives a burst on a hit and <b>Blackout</b> drops the wall to black."],
         tips=["Haze breathes with the music", "Flash on the drop", "Blackout before a hit"]),
    dict(slug="band-name", tile="Name & lyrics", tile_text="Any font, glow on cue, silhouette style, lyrics on their own lane.",
         eyebrow="Your name in lights", title="Your band's name, glowing on cue.",
         lead="Type your name, pick a font and a layout, and decide when it shows. A Band Name Light lane makes "
              "it glow and flash on the hits.",
         video="rec-bandname", image="ui-bandname",
         steps=["In <b>Elements</b>, select <b>★ Band name</b> at the top of the list and type the name.",
                "Pick one of the bundled fonts (they look the same on every computer) or any font you have installed, "
                "then the layout, letter case and colour.",
                "Tick <b>Always on</b> to keep it lit the whole song, or bring it in with notes on the Global "
                "<b>Band Name</b> lane.",
                "Put notes on the <b>Band Name Light</b> lane to make it glow: short notes flash, long notes glow.",
                "<b>Dark cut-out</b> turns the name into a silhouette against the light behind it."],
         tips=["Drag the name in the preview", "Mouse wheel resizes it", "Lyrics have their own font"]),
    dict(slug="stage-preview", tile="Stage simulator", tile_text="Your band in silhouette, LED wall or projector, house lights.",
         eyebrow="Stage preview", title="See it on stage before the show.",
         lead="Watch the whole video behind a simulated band, one silhouette per member, with an audience in "
              "front. Compare an LED wall with a projector, and add the venue's own lights.",
         video="rec-stage", image="rec-stage",
         steps=["Open the <b>Export</b> tab and press <b>Play from the start</b>.",
                "Switch <b>View</b> to <b>On stage: LED wall</b>: the wall lights the band from behind.",
                "Or pick <b>On stage: projector</b>: the picture comes from the front, lands on the band, and they "
                "cast shadows on the screen.",
                "Tick <b>House lights</b> to add the venue's moving lights, the ones you don't control, and set how strong they are.",
                "<b>Open big preview window</b> and press F11 for fullscreen, to show the band."],
         tips=["One silhouette per member, in Band order", "Space = play / pause", "Esc closes the big window"]),
    dict(slug="export", tile="Fast 4K export", tile_text="3840×1080 at 60 fps, encoded on your graphics card.",
         eyebrow="Export", title="A 4K video, ready for the show.",
         lead="Render the whole song or a seamless loop at 3840×1080, Full HD or any size. Frames are drawn on "
              "several cores and encoded on your graphics card when it has a video encoder.",
         video="rec-export", image="ui-export",
         steps=["In <b>Export</b>, pick the resolution (3840×1080 for a wide LED wall, 1920×1080, or a custom size) and 30 or 60 fps.",
                "The <b>Range</b> is the loop region from the Master Track, or the whole song if there isn't one.",
                "Leave <b>Encode on the graphics card</b> on: the Encoder line shows what your computer will use.",
                "Tick <b>Mux audio</b> to put the song in the video too.",
                "Click <b>Render Video</b> and choose where to save the MP4."],
         tips=["NVIDIA, Apple, Intel and AMD encoders", "H.264 MP4 plays everywhere", "Renders go into the project folder"]),
    dict(slug="projects", tile="Tidy projects", tile_text="One folder per song with its audio and automatic backups.",
         eyebrow="Projects", title="Every song in its own folder.",
         lead="Like in a music app, each project gets a folder with its audio and backups, so you can copy a "
              "whole show to a USB stick and open it on another computer.",
         video=None, image="ui-projects",
         steps=["<b>File → New Project</b> asks for a name and where to keep it (Documents/LED Wall Creator by default).",
                "Leave <b>Create a folder for the project</b> on: you get the project file plus <b>Media</b> and <b>Backups</b> folders.",
                "Loaded audio is copied into Media, and stored relative to the project so the folder can move.",
                "Every save keeps the previous version in Backups (the newest 20).",
                "Double-click a <b>.ledwall</b> file to open it. <b>File → Open Recent</b> has your last projects."],
         tips=["Backups open like any project", "Show Project Folder in the File menu", "Old .json projects still open"]),
    dict(slug="updates", tile="Updates itself", tile_text="New versions install with one click. Your projects stay safe.",
         eyebrow="Updates", title="Always the latest, in one click.",
         lead="When a new version comes out, the app tells you when it starts. One click downloads it, installs "
              "it next to the current one and restarts. Your projects are never touched.",
         video=None, image="ui-updates",
         steps=["When the app starts it checks quietly for a new version (it stays silent when you're offline).",
                "If there is one, you see what's new and choose <b>Install update</b>, <b>Later</b> or <b>Skip this version</b>.",
                "It downloads, installs, restarts into the new version and removes the old one.",
                "Anything you kept inside the app's folder is moved to the new version first. If a file can't be "
                "moved, the old version is kept.",
                "<b>Help → Check for Updates</b> checks by hand, and the automatic check can be turned off."],
         tips=["You download only once", "Works on Windows, macOS and Linux"]),
]

FOCUS = {
    "pattern-editor": "--fx:62%;--fy:55%;--z:1.7",
    "master-track": "--fx:62%;--fy:50%;--z:1.6",
    "elements": "--fx:55%;--fy:35%;--z:1.5",
    "custom-shapes": "--fx:55%;--fy:22%;--z:1.7",
    "glow-haze": "--fx:50%;--fy:10%;--z:2.2",
    "band-name": "--fx:50%;--fy:9%;--z:2.4",
    "stage-preview": "--fx:50%;--fy:9%;--z:2.6",
    "export": "--fx:74%;--fy:80%;--z:1.8",
    "projects": "--fx:50%;--fy:38%;--z:2.1",
    "updates": "--fx:50%;--fy:55%;--z:1.9",
}


NAV = '''<header class="nav">
  <a class="brand" href="{r}./"><img src="{r}assets/img/logo.png" alt=""><span>LED Wall Creator</span></a>
  <nav class="nav-links">
    <a href="{r}./">Home</a>
    <a href="{r}download.html">Download</a>
    <a href="{r}story.html">Story</a>
  </nav>
  <a class="btn btn-primary btn-sm js-download" href="{r}download.html">Download</a>
</header>'''

FOOTER = '''<footer class="footer">
  <div class="wrap">
    <a class="brand" href="{r}./"><img src="{r}assets/img/logo.png" alt=""><span>LED Wall Creator</span></a>
    <nav><a href="{r}./">Home</a><a href="{r}download.html">Download</a><a href="{r}story.html">Story</a>
      <a href="https://github.com/HenriqueSim/LED_WALL_CREATOR" rel="noopener">Source code</a></nav>
    <span>Free &amp; open source (MIT) · made by a band, for bands</span>
  </div>
</footer>'''

DL_ICON = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" '
           'stroke-linejoin="round"><path d="M12 4v11m0 0l-4.5-4.5M12 15l4.5-4.5M5 20h14"/></svg>')


def thumb(f):
    return (f["video"] or f["image"]) + ".jpg"


def page(f, i):
    r = "../"
    prev_f, next_f = FEATURES[i - 1], FEATURES[(i + 1) % len(FEATURES)]
    if f["video"]:
        media = (f'<video class="fhero-video" src="{r}media/{f["video"]}.mp4" poster="{r}media/{f["video"]}.jpg" '
                 f'muted loop playsinline autoplay preload="auto"></video>')
    else:
        media = f'<img class="fhero-video still" src="{r}media/{f["image"]}.jpg" alt="">'
    steps = "\n".join(f"          <li>{s}</li>" for s in f["steps"])
    tips = "".join(f'<span class="chip">{html.escape(t)}</span>' for t in f["tips"])
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>{html.escape(f["eyebrow"])} · LED Wall Creator</title>
  <meta name="description" content="{html.escape(f["lead"])}">
  <meta name="theme-color" content="#07070a">
  <link rel="icon" href="{r}assets/img/favicon.png">
  <link rel="preload" href="{r}assets/fonts/Inter-Variable.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="stylesheet" href="{r}assets/style.css">
</head>
<body data-page="feature">

{NAV.format(r=r)}

<section class="fhero">
  {media}
  <div class="wrap fhero-text">
    <a class="back reveal" href="{r}./#features">← All features</a>
    <span class="eyebrow reveal"><span class="dot"></span>{html.escape(f["eyebrow"])}</span>
    <h1 class="reveal">{html.escape(f["title"])}</h1>
    <p class="lead reveal">{html.escape(f["lead"])}</p>
    <div class="cta reveal" style="justify-content:flex-start">
      <a class="btn btn-primary js-download" href="{r}download.html">{DL_ICON}<span class="js-download-label">Download free</span></a>
      <a class="btn" href="#how">How it works</a>
    </div>
  </div>
</section>

<section class="section" id="how">
  <div class="wrap how">
    <div class="how-text">
      <span class="eyebrow reveal"><span class="dot"></span>How it works</span>
      <h2 class="reveal">In the app</h2>
      <ol class="steps big reveal">
{steps}
      </ol>
      <div class="chips reveal" style="margin-top:22px">{tips}</div>
    </div>
    <figure class="how-shot reveal">
      <img src="{r}media/{f["image"]}.jpg" alt="{html.escape(f["eyebrow"])} in LED Wall Creator" loading="lazy">
    </figure>
  </div>
</section>

<section class="section" style="padding-top:0">
  <div class="wrap pager">
    <a class="pager-card reveal" href="{prev_f["slug"]}.html"><span class="muted">← Previous</span><b>{html.escape(prev_f["tile"])}</b></a>
    <a class="pager-card reveal" href="{r}./#features"><span class="muted">Overview</span><b>All features</b></a>
    <a class="pager-card reveal right" href="{next_f["slug"]}.html"><span class="muted">Next →</span><b>{html.escape(next_f["tile"])}</b></a>
  </div>
</section>

{FOOTER.format(r=r)}

<script src="{r}assets/site.js" defer></script>
</body>
</html>
'''


def grid():
    out = []
    for f in FEATURES:
        out.append(f'''      <a class="tile reveal" href="features/{f["slug"]}.html">
        <div class="thumb"><img src="media/{thumb(f)}" alt="" loading="lazy" style="{FOCUS.get(f["slug"], "")}"></div>
        <h3>{html.escape(f["tile"])} <span class="arrow">→</span></h3><p>{html.escape(f["tile_text"])}</p>
      </a>''')
    return "\n".join(out)


def main():
    os.makedirs(os.path.join(SITE, "features"), exist_ok=True)
    for i, f in enumerate(FEATURES):
        with open(os.path.join(SITE, "features", f["slug"] + ".html"), "w") as fh:
            fh.write(page(f, i))
    p = os.path.join(SITE, "index.html")
    s = open(p).read()
    s = re.sub(r"(<!-- features:start -->).*?(\s*<!-- features:end -->)", lambda m: m.group(1) + "\n" + grid() + m.group(2),
               s, flags=re.S)
    open(p, "w").write(s)
    print(f"{len(FEATURES)} feature pages + home grid")


if __name__ == "__main__":
    main()
