"""Song data model: band (members + groups), patterns (piano-roll notes), master track
(clips + lyrics) and the per-song look. Pure Python - no Qt, no OpenCV."""

from __future__ import annotations

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
    ("logo",     "Band Name",   "Shows the band name layer"),
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
]
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
    the members in order. `visible` = available in this project."""
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
    core: float = 0.5            # sun: how much the centre glows towards white
    softness: float = 0.025      # sun: edge feather (fraction of the radius)
    cuts: int = 0                # sun: horizontal cut lines near the horizon


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
    }
    return Element(new_id(), **presets.get(kind, presets["polygon"]))


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
    # band name layer (stacks on top of any background)
    logo_text: str = "vespa maia"
    logo_stacked: bool = True
    logo_size: float = 0.40                 # fraction of the wall height
    logo_y: float = 0.58                    # vertical centre
    logo_color: str = "accent"              # accent | main | white
    logo_attack_ms: float = 450.0
    logo_release_ms: float = 1600.0
    logo_peak: float = 1.0
    logo_peak_decay_ms: float = 700.0
    logo_glow_radius: float = 38.0
    logo_idle: float = 0.0
    lyrics_size: float = 0.11


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
    def active_elements(self) -> list:
        return [e for e in self.elements if e.visible]

    def element_sublanes(self, el) -> list:
        """[(lane key, label, kind)] inside one element's folder."""
        out = [(el_lane(el.id, "all"), "All", "all")]
        out += [(el_lane(el.id, f"m:{m.id}"), m.name, "member") for m in self.members]
        out += [(el_lane(el.id, f"g:{g.id}"), g.name, "group") for g in self.groups]
        out += [(el_lane(el.id, k), label, "fx") for k, label, _ in ELEMENT_FX]
        return out

    def lanes(self) -> list:
        """Every note lane, flat: [(lane key, label, kind)]."""
        out = []
        for el in self.active_elements():
            out += self.element_sublanes(el)
        out += [(f"fx:{k}", label, "logo" if k == "logo" else "global") for k, label, _ in GLOBAL_FX]
        return out

    def lane_rows(self, collapsed=frozenset()) -> list:
        """Rows of the pattern editor: folder rows ("fold:<eid>", name, "folder") followed by their
        lanes unless collapsed, then the global folder."""
        rows = []
        for el in self.active_elements():
            rows.append((f"fold:{el.id}", el.name, "folder"))
            if el.id not in collapsed:
                rows += self.element_sublanes(el)
        rows.append(("fold:global", "Global", "folder"))
        if "global" not in collapsed:
            rows += [(f"fx:{k}", label, "logo" if k == "logo" else "global") for k, label, _ in GLOBAL_FX]
        return rows

    def pattern(self, pid: str) -> Pattern | None:
        return next((p for p in self.patterns if p.id == pid), None)

    def member_index(self) -> dict:
        return {m.id: i for i, m in enumerate(self.members)}

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

    def element(self, eid: str) -> Element | None:
        return next((e for e in self.elements if e.id == eid), None)

    def remove_pattern(self, pid: str):
        self.patterns = [p for p in self.patterns if p.id != pid]
        self.clips = [c for c in self.clips if c.pattern_id != pid]

    # ---- (de)serialisation ---------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d["version"] = 3
        return d

    @staticmethod
    def from_dict(d: dict) -> "Song":
        if d.get("version") not in (2, 3):
            raise ValueError("This project was made with an older version of the app (no patterns).")

        def tup(v):
            return tuple(v) if isinstance(v, list) else v

        raw_look = d.get("look", {})
        known = {f.name for f in fields(Look)}
        look = Look(**{k: tup(v) for k, v in raw_look.items() if k in known})
        ek = {f.name for f in fields(Element)}
        n_mem = max(1, len(d.get("members", [])))
        if "elements" in d:
            elements = [Element(**{k: tup(v) for k, v in e.items() if k in ek}) for e in d["elements"]]
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
        patterns = [Pattern(id=p["id"], name=p["name"], bars=p.get("bars", 1), color=tup(p.get("color", (255, 160, 40))),
                            notes=[Note(**n) for n in p.get("notes", [])]) for p in d.get("patterns", [])]
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
                      Note("fx:logo", 0, 32, 1.0), Note("fx:flash", 0, 1, 0.6)])
    build = Pattern(new_id(), "Build", 1, PATTERN_COLORS[3],
                    [Note(S("all"), 0, 16, 0.5), Note(B("chase"), 0, 12, 0.9), Note(B("strobe"), 12, 4, 0.8)])

    clips = [
        Clip(new_id(), intro.id, 0, 0.0, 16.0),
        Clip(new_id(), groove.id, 0, 16.0, 28.0),
        Clip(new_id(), build.id, 0, 44.0, 4.0),
        Clip(new_id(), chorus.id, 0, 48.0, 32.0),
    ]
    lyrics = [LyricClip(new_id(), "lyrics go here in the chorus", 48.0, 16.0)]
    look = Look(logo_color="cutout")
    return Song(members, groups, [intro, groove, chorus, build], clips, lyrics, [beam, sun], look)
