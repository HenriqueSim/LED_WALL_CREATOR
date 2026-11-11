"""Song data model: band (members + groups), patterns (piano-roll notes), master track
(clips + lyrics) and the per-song look. Pure Python - no Qt, no OpenCV."""

from __future__ import annotations

import copy
import uuid
from dataclasses import asdict, dataclass, field, fields

STEPS_PER_BEAT = 4          # pattern grid resolution = 1/16 notes
BEATS_PER_BAR = 4
STEPS_PER_BAR = STEPS_PER_BEAT * BEATS_PER_BAR
N_TRACKS = 3                # pattern tracks on the master track

# Lanes. Every element of the song is a folder in the pattern editor holding its own lanes:
#   e:<element id>|all        the whole element
#   e:<element id>|m:<id>     one member's part of it
#   e:<element id>|g:<id>     a group's parts
#   e:<element id>|<fx>       motion modes on that element (pulse, chase, random, strobe)
# Global lanes (not tied to an element) are "fx:<key>".
#   key        label           help text
ELEMENT_FX = [
    ("pulse",    "Pulse",       "Beat-synced pump"),
    ("chase",    "Chase",       "Runs across the parts, one per 1/16"),
    ("random",   "Random",      "Random parts flicker every 1/16"),
    ("strobe",   "Strobe",      "Fast flashes (rate in Look tab)"),
]
GLOBAL_FX = [
    ("sparkle",  "Sparkle",     "Small glints across the wall"),
    ("flash",    "Flash Peak",  "Bloom burst in the accent colour on whatever is in the scene"),
    ("blackout", "Blackout",    "Kills the elements (band name + lyrics stay)"),
    ("logo",     "Band Name",   "Shows the band name (not needed when it is set to always on)"),
    ("logo_glow", "Band Name Light", "Lights up / flashes the band name: a glow while the note lasts"),
]
FX_LANES = GLOBAL_FX
_OLD_ELEMENT_LANES = ("fx:all", "fx:pulse", "fx:chase", "fx:random", "fx:strobe")


def el_lane(eid: str, sub: str) -> str:
    return f"e:{eid}|{sub}"


def split_lane(lane: str):
    """'e:<eid>|<sub>' -> (eid, sub); global lanes -> (None, lane)."""
    if lane.startswith("e:") and "|" in lane:
        eid, sub = lane[2:].split("|", 1)
        return eid, sub
    return None, lane


PATTERN_COLORS = [
    (255, 160, 40), (90, 200, 255), (255, 90, 120), (140, 230, 120),
    (200, 140, 255), (255, 220, 90), (90, 240, 210), (255, 130, 200),
]

ELEMENT_TYPES = [
    ("sun",     "Sunset Sun"),
    ("polygon", "Polygon / Hexagon"),
    ("ring",    "Ring / Circle"),
    ("bars",    "Vertical Blocks"),
    ("shafts",  "Light Shafts"),
    ("horizon", "Horizon"),
    ("beam",    "Horizontal Beam / Line"),
    ("custom",  "Custom (your own shapes)"),
]

# building blocks of a custom element
SHAPE_KINDS = [
    ("line",    "Line"),
    ("dashes",  "Dashed line"),
    ("rect",    "Rectangle"),
    ("circle",  "Circle"),
    ("arc",     "Arc / half circle"),
    ("polygon", "Polygon / triangle"),
    ("rays",    "Rays"),
]
SHAPE_LABELS = dict(SHAPE_KINDS)


def new_shape(kind: str, **kw) -> dict:
    """One shape of a custom element. Positions and sizes are in units of the element's scale
    (x the wall height), relative to the element's centre."""
    base = dict(kind=kind, x=0.0, y=0.0, size=0.6, h=0.3, rot=0.0, sides=3, thickness=5.0, filled=False,
                dashes=8, sweep=180.0)
    base.update({"line": dict(size=1.0), "dashes": dict(size=1.0, dashes=8), "rect": dict(size=0.8, h=0.5),
                 "circle": dict(size=0.4), "arc": dict(size=0.6, rot=180.0, sweep=180.0),
                 "polygon": dict(size=0.5, sides=3), "rays": dict(size=1.0, h=0.55, dashes=9, rot=180.0, sweep=180.0),
                 }.get(kind, {}))
    base.update(kw)
    return base
ELEMENT_LABELS = dict(ELEMENT_TYPES)


def new_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class Member:
    id: str
    name: str


@dataclass
class Group:
    id: str
    name: str
    members: list = field(default_factory=list)   # member ids


@dataclass
class Note:
    lane: str            # "e:<element id>|<sub lane>" or "fx:<global key>" (see el_lane)
    start: int           # in steps (1/16)
    length: int          # in steps
    velocity: float = 1.0


@dataclass
class Pattern:
    id: str
    name: str
    bars: int = 1
    color: tuple = (255, 160, 40)
    notes: list = field(default_factory=list)
    elements: list | None = None     # ids of the song's elements this pattern uses (None = all of them)
    fx_speed: float = 1.0            # Pulse / Chase / Random / Strobe speed: 0.25 .. 4 x the song tempo

    def uses(self, eid: str) -> bool:
        return self.elements is None or eid in self.elements

    @property
    def steps(self) -> int:
        return self.bars * STEPS_PER_BAR

    @property
    def beats(self) -> float:
        return float(self.bars * BEATS_PER_BAR)


@dataclass
class Clip:
    id: str
    pattern_id: str
    track: int
    start: float          # beats from the song grid origin
    length: float         # beats; longer than the pattern = pattern loops


@dataclass
class LyricClip:
    id: str
    text: str
    start: float          # beats
    length: float         # beats


@dataclass
class Element:
    """One visual element of the wall. It is lit by its own lanes in the pattern editor and is only
    in the scene while one of them plays. Its parts (edges, bars, segments...) are shared out between
    the members in order. Elements are shared by the whole song; each pattern chooses which ones it
    uses (Pattern.elements). `visible` is only kept for loading old projects."""
    id: str
    name: str
    type: str = "polygon"
    visible: bool = True
    driver: str = "members"      # unused since element lanes; kept so old projects load
    x: float = 0.5               # centre, fraction of the wall width
    y: float = 0.6               # centre, fraction of the wall height
    size: float = 0.3            # polygon / ring radius (x height) · bars / shafts / horizon / beam width (x width)
    height: float = 0.7          # bars / shafts: tallest point (x height)
    rotation: float = 0.0        # degrees
    spin: float = 0.0            # degrees per beat
    sides: int = 6
    thickness: float = 5.0       # px at 1080p
    fill: str = "outline"        # outline | filled | gradient
    color: str = "main"          # main | accent | custom
    custom_rgb: tuple = (255, 255, 255)
    glow: float = 1.0
    idle: float = 0.12
    react_scale: float = 0.08    # polygon / ring grow with the level
    slats: int = 3               # old: bars per member (migrated to count)
    count: int = 6               # parts: bars / shafts / lights / ring arcs / beam segments
    gap: float = 0.28            # bars: gap between bars (x bar width)
    lines: int = 1               # beam: parallel lines
    spacing: float = 0.04        # beam: distance between parallel lines (x height)
    preset: str = ""             # preset it was made from (its settings are the reset defaults)
    shapes: list = field(default_factory=list)   # custom: list of new_shape() dicts, one part each
    core: float = 0.5            # sun: how much the centre glows towards white
    softness: float = 0.025      # sun: edge feather (fraction of the radius)
    cuts: int = 0                # sun: horizontal cut lines near the horizon
    music: float = 1.0           # haze: how much it breathes with the music's loudness


def preset_element(kind: str) -> Element:
    """Ready-made elements offered in the Elements tab."""
    presets = {
        "sun": dict(name="Sun", type="sun", x=0.5, y=1.0, size=0.82, idle=0.72, glow=0.35, react_scale=0.012,
                    core=0.55, softness=0.025),
        "hexagon": dict(name="Main hexagon", type="polygon", sides=6, x=0.5, y=0.58, size=0.3, thickness=7,
                        fill="outline", glow=1.3, idle=0.15),
        "polygon": dict(name="Polygon", type="polygon", sides=6, x=0.5, y=0.58, size=0.25),
        "ring": dict(name="Ring", type="ring", x=0.5, y=0.58, size=0.28, thickness=6, count=6),
        "bars": dict(name="Vertical blocks", type="bars", x=0.5, y=1.0, size=0.94, height=0.72, fill="gradient",
                     glow=0.45, idle=0.12, thickness=2, count=18),
        "bar": dict(name="Single bar", type="bars", x=0.5, y=1.0, size=0.05, height=0.7, fill="gradient",
                    glow=0.6, idle=0.15, thickness=2, count=1),
        "shafts": dict(name="Light shafts", type="shafts", x=0.5, y=1.0, size=0.94, height=0.72, glow=0.7, idle=0.12,
                       count=6),
        "horizon": dict(name="Horizon", type="horizon", x=0.5, y=0.84, size=0.9, height=0.6, idle=0.12, count=6),
        "beam": dict(name="Horizontal beam", type="beam", x=0.5, y=0.9, size=0.8, thickness=6, idle=0.2, count=1),
        "haze": dict(name="Haze", type="haze", x=0.5, y=1.0, size=0.55, height=0.38, glow=1.5, idle=0.0,
                     music=1.0),
        "custom": dict(name="Custom element", type="custom", x=0.5, y=0.6, size=0.3, idle=0.15,
                       shapes=[new_shape("polygon", sides=6, size=0.8, thickness=6)]),
        "custom_rays": dict(name="Rising rays", type="custom", x=0.5, y=0.97, size=0.55, idle=0.15, glow=0.9,
                            shapes=[new_shape("arc", size=0.45, thickness=6, rot=180.0, sweep=180.0),
                                    new_shape("rays", size=1.1, h=0.6, dashes=9, thickness=4),
                                    new_shape("line", size=3.2, thickness=3)]),
    }
    kind = kind if kind in presets else "polygon"
    return Element(new_id(), preset=kind, **copy.deepcopy(presets[kind]))


_TYPE_PRESET = {"sun": "sun", "polygon": "polygon", "ring": "ring", "bars": "bars", "shafts": "shafts",
                "horizon": "horizon", "beam": "beam", "custom": "custom", "haze": "haze"}


def element_defaults(el: Element) -> Element:
    """The default settings for an element: those of the preset it came from."""
    d = preset_element(el.preset or _TYPE_PRESET.get(el.type, "polygon"))
    if d.type != el.type:
        d = preset_element(_TYPE_PRESET.get(el.type, "polygon"))
    return d


@dataclass
class Look:
    main_rgb: tuple = (255, 186, 128)       # THE song colour, shared by every member
    accent_rgb: tuple = (255, 240, 215)     # flashes / strobes / peaks
    glow_radius: float = 26.0
    glow_intensity: float = 1.0
    attack_ms: float = 30.0
    release_ms: float = 380.0
    audio_react: float = 0.35
    sensitivity: float = 0.55
    headroom: float = 0.24                  # top fraction kept free for lyrics
    strobe_rate: int = 4                    # flashes per beat
    ambient: float = 0.0                    # old: the always-on wash, now a Haze element (see from_dict)
    ambient_pulse: float = 1.0              # old: its music pulse
    # band name layer (stacks on top of any background)
    logo_text: str = "band name"
    logo_stacked: bool = False              # old flag, see logo_layout
    logo_layout: str = "line"               # line | stacked (2nd line offset right) | two_lines (centred)
    logo_dot: bool = True                   # stacked: full stop after the first line
    logo_offset: float = 0.456              # stacked: 2nd line shift (x the first line's width)
    logo_font: str = "Anton"
    logo_case: str = "asis"                 # asis | lower | upper
    logo_size: float = 0.40                 # fraction of the wall height
    logo_x: float = 0.5                     # horizontal centre
    logo_y: float = 0.58                    # vertical centre
    logo_color: str = "accent"              # accent | main | white
    logo_attack_ms: float = 450.0
    logo_release_ms: float = 1600.0
    logo_peak: float = 1.0
    logo_peak_decay_ms: float = 700.0
    logo_glow_radius: float = 38.0
    logo_idle: float = 0.0                  # how visible the name stays between Band Name notes
    logo_always: bool = False               # shown at full strength for the whole song (no fades)
    logo_glow: float = 1.0                  # Band Name Light: glow while a note is held
    logo_glow_attack_ms: float = 120.0
    logo_glow_release_ms: float = 700.0
    lyrics_size: float = 0.11
    lyrics_font: str = ""                   # "" = same as the band name


@dataclass
class Song:
    members: list = field(default_factory=list)
    groups: list = field(default_factory=list)
    patterns: list = field(default_factory=list)
    clips: list = field(default_factory=list)
    lyrics: list = field(default_factory=list)
    elements: list = field(default_factory=list)
    look: Look = field(default_factory=Look)
    bpm: float = 120.0
    beat_offset: float = 0.0
    audio_path: str | None = None
    region: tuple | None = None             # loop / export region in seconds
    loop: bool = False

    # ---- lookups -----------------------------------------------------------
    def pattern_elements(self, pat) -> list:
        """The elements a pattern uses, in layer order (all of them when pat is None)."""
        if pat is None:
            return list(self.elements)
        return [e for e in self.elements if pat.uses(e.id)]

    def element_sublanes(self, el) -> list:
        """[(lane key, label, kind)] inside one element's folder."""
        if el.type == "haze":       # no parts: one lane, note velocity = how much haze
            return [(el_lane(el.id, "all"), "Haze amount", "all")]
        out = [(el_lane(el.id, "all"), "All", "all")]
        out += [(el_lane(el.id, f"m:{m.id}"), m.name, "member") for m in self.members]
        out += [(el_lane(el.id, f"g:{g.id}"), g.name, "group") for g in self.groups]
        out += [(el_lane(el.id, k), label, "fx") for k, label, _ in ELEMENT_FX]
        return out

    def lanes(self, pat=None) -> list:
        """Every note lane of a pattern (or of all elements), flat: [(lane key, label, kind)]."""
        out = []
        for el in self.pattern_elements(pat):
            out += self.element_sublanes(el)
        out += [(f"fx:{k}", label, "logo" if k.startswith("logo") else "global") for k, label, _ in GLOBAL_FX]
        return out

    def lane_rows(self, pat=None, collapsed=frozenset()) -> list:
        """Rows of the pattern editor for a pattern: a folder row ("fold:<eid>", name, "folder") per
        element it uses, followed by its lanes unless collapsed, then the global folder."""
        rows = []
        for el in self.pattern_elements(pat):
            rows.append((f"fold:{el.id}", el.name, "folder"))
            if el.id not in collapsed:
                rows += self.element_sublanes(el)
        rows.append(("fold:global", "Global", "folder"))
        if "global" not in collapsed:
            rows += [(f"fx:{k}", label, "logo" if k.startswith("logo") else "global") for k, label, _ in GLOBAL_FX]
        return rows

    def pattern(self, pid: str) -> Pattern | None:
        return next((p for p in self.patterns if p.id == pid), None)

    def member_index(self) -> dict:
        return {m.id: i for i, m in enumerate(self.members)}

    def set_timing(self, bpm: float | None = None, beat_offset: float | None = None):
        """Change the tempo and / or where bar 1 sits in the audio. The loop region stays on the same
        bars (it is stored in seconds of audio, so it is converted through beats)."""
        old_p, old_o = 60.0 / max(self.bpm, 1.0), self.beat_offset
        region_b = None
        if self.region:
            region_b = tuple((t - old_o) / old_p for t in self.region)
        if bpm is not None:
            self.bpm = float(bpm)
        if beat_offset is not None:
            self.beat_offset = float(beat_offset)
        if region_b is not None:
            p = 60.0 / max(self.bpm, 1.0)
            self.region = tuple(self.beat_offset + b * p for b in region_b)

    def song_end_beats(self) -> float:
        ends = [c.start + c.length for c in self.clips] + [l.start + l.length for l in self.lyrics]
        return max(ends) if ends else 0.0

    def remove_member(self, mid: str):
        self.members = [m for m in self.members if m.id != mid]
        for g in self.groups:
            g.members = [x for x in g.members if x != mid]
        for p in self.patterns:
            p.notes = [n for n in p.notes if split_lane(n.lane)[1] != f"m:{mid}"]

    def remove_group(self, gid: str):
        self.groups = [g for g in self.groups if g.id != gid]
        for p in self.patterns:
            p.notes = [n for n in p.notes if split_lane(n.lane)[1] != f"g:{gid}"]

    def remove_element(self, eid: str):
        self.elements = [e for e in self.elements if e.id != eid]
        for p in self.patterns:
            p.notes = [n for n in p.notes if split_lane(n.lane)[0] != eid]
            if p.elements is not None:
                p.elements = [x for x in p.elements if x != eid]

    def element(self, eid: str) -> Element | None:
        return next((e for e in self.elements if e.id == eid), None)

    def remove_pattern(self, pid: str):
        self.patterns = [p for p in self.patterns if p.id != pid]
        self.clips = [c for c in self.clips if c.pattern_id != pid]

    # ---- (de)serialisation ---------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d["version"] = 6
        return d

    @staticmethod
    def from_dict(d: dict) -> "Song":
        if d.get("version") not in (2, 3, 4, 5, 6):
            raise ValueError("This project was made with an older version of the app (no patterns).")

        def tup(v):
            return tuple(v) if isinstance(v, list) else v

        raw_look = d.get("look", {})
        known = {f.name for f in fields(Look)}
        look = Look(**{k: tup(v) for k, v in raw_look.items() if k in known})
        if "logo_layout" not in raw_look:   # before layouts: the stacked flag
            look.logo_layout = "stacked" if raw_look.get("logo_stacked", True) else "line"
        ek = {f.name for f in fields(Element)}
        n_mem = max(1, len(d.get("members", [])))
        if "elements" in d:
            elements = [Element(**{k: (list(v) if k == "shapes" else tup(v)) for k, v in e.items() if k in ek})
                        for e in d["elements"]]
            for e, raw in zip(elements, d["elements"]):
                if "count" not in raw:   # before per-element part counts
                    e.count = e.slats * n_mem if e.type == "bars" else n_mem
        else:   # projects saved before the Elements tab: rebuild from the old single design
            design = raw_look.get("design", "blocks")
            elements = [preset_element(design if design != "blocks" else "bars")] if design != "none" else []
            for e in elements:
                e.idle = raw_look.get("idle_level", e.idle)
                e.slats = raw_look.get("slats_per_member", e.slats)
                e.count = e.slats * n_mem if e.type == "bars" else n_mem
        # before per-pattern elements: every pattern used the elements switched on for the project
        old_on = [e.id for e in elements if e.visible]
        patterns = [Pattern(id=p["id"], name=p["name"], bars=p.get("bars", 1), color=tup(p.get("color", (255, 160, 40))),
                            fx_speed=float(p.get("fx_speed", 1.0)),
                            notes=[Note(**n) for n in p.get("notes", [])],
                            elements=list(p["elements"]) if p.get("elements") is not None else
                            (None if "elements" in p else list(old_on)))
                    for p in d.get("patterns", [])]
        if d.get("version") == 2:   # member / group / All / motion lanes now live inside each element
            targets = [e.id for e in elements if e.visible] or [e.id for e in elements]
            for p in patterns:
                new = []
                for n in p.notes:
                    if n.lane.startswith(("m:", "g:")) or n.lane in _OLD_ELEMENT_LANES:
                        sub = n.lane[3:] if n.lane.startswith("fx:") else n.lane
                        new += [Note(el_lane(eid, sub), n.start, n.length, n.velocity) for eid in targets]
                    else:
                        new.append(n)
                p.notes = new
        if d.get("version", 0) < 5 and look.logo_peak > 0:
            # the glow peak used to fire at the start of every Band Name note; it now has its own
            # Band Name Light lane, so give the old notes a short light note at the same spot
            for p in patterns:
                p.notes += [Note("fx:logo_glow", n.start, 1, n.velocity) for n in p.notes if n.lane == "fx:logo"]
        if d.get("version", 0) < 6:
            # the ambient wash used to be always on (a Look setting); it is now a Haze element that
            # each pattern switches on with a note. Old projects get one, on in every pattern.
            amb = float(raw_look.get("ambient", 1.0))
            if amb > 0 and patterns:
                haze = preset_element("haze")
                haze.glow, haze.music = amb, float(raw_look.get("ambient_pulse", 1.0))
                elements.append(haze)
                for p in patterns:
                    if p.elements is not None:
                        p.elements.append(haze.id)
                    p.notes.append(Note(el_lane(haze.id, "all"), 0, p.bars * STEPS_PER_BAR, 1.0))
            look.ambient = 0.0
        return Song(
            members=[Member(**m) for m in d.get("members", [])],
            groups=[Group(**g) for g in d.get("groups", [])],
            patterns=patterns,
            clips=[Clip(**c) for c in d.get("clips", [])],
            lyrics=[LyricClip(**l) for l in d.get("lyrics", [])],
            elements=elements,
            look=look,
            bpm=d.get("bpm", 120.0),
            beat_offset=d.get("beat_offset", 0.0),
            audio_path=d.get("audio_path"),
            region=tup(d.get("region")),
            loop=bool(d.get("loop", False)),
        )


def default_song() -> Song:
    """Six members, two groups, a sunset sun and a horizontal beam, and a few starter patterns."""
    members = [Member(new_id(), f"Member {i + 1}") for i in range(6)]
    ids = [m.id for m in members]
    groups = [Group(new_id(), "Left side", ids[:3]), Group(new_id(), "Right side", ids[3:])]
    sun, beam = preset_element("sun"), preset_element("beam")
    beam.count = 6
    S, B = (lambda sub: el_lane(sun.id, sub)), (lambda sub: el_lane(beam.id, sub))

    intro = Pattern(new_id(), "Intro sunrise", 2, PATTERN_COLORS[0],
                    [Note(S("all"), 0, 32, 0.55), Note(S("pulse"), 16, 16, 0.6)])
    groove = Pattern(new_id(), "Groove", 1, PATTERN_COLORS[1],
                     [Note(S("all"), 0, 16, 0.7), Note(B("pulse"), 0, 16, 0.7)] +
                     [Note(B(f"g:{groups[i % 2].id}"), i * 4, 2, 0.9) for i in range(4)])
    chorus = Pattern(new_id(), "Chorus", 2, PATTERN_COLORS[2],
                     [Note(S("all"), 0, 32, 1.0), Note(S("pulse"), 0, 32, 0.8), Note(B("all"), 0, 32, 0.8),
                      Note("fx:logo", 0, 32, 1.0), Note("fx:logo_glow", 0, 1, 1.0),
                      Note("fx:flash", 0, 1, 0.6)])
    build = Pattern(new_id(), "Build", 1, PATTERN_COLORS[3],
                    [Note(S("all"), 0, 16, 0.5), Note(B("chase"), 0, 12, 0.9), Note(B("strobe"), 12, 4, 0.8)])

    haze = preset_element("haze")
    intro.elements = [sun.id, haze.id]                 # the intro is just the sun (and some haze)
    for p in (groove, chorus, build):
        p.elements = [beam.id, sun.id, haze.id]
    for p, v in ((intro, 0.7), (groove, 0.8), (chorus, 1.0), (build, 0.6)):
        p.notes.append(Note(el_lane(haze.id, "all"), 0, p.bars * STEPS_PER_BAR, v))

    clips = [
        Clip(new_id(), intro.id, 0, 0.0, 16.0),
        Clip(new_id(), groove.id, 0, 16.0, 28.0),
        Clip(new_id(), build.id, 0, 44.0, 4.0),
        Clip(new_id(), chorus.id, 0, 48.0, 32.0),
    ]
    lyrics = [LyricClip(new_id(), "lyrics go here in the chorus", 48.0, 16.0)]
    look = Look(logo_color="cutout")
    return Song(members, groups, [intro, groove, chorus, build], clips, lyrics, [beam, sun, haze], look)
