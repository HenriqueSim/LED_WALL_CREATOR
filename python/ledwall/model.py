"""Song data model: band (members + groups), patterns (piano-roll notes), master track
(clips + lyrics) and the per-song look. Pure Python - no Qt, no OpenCV."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, fields

STEPS_PER_BEAT = 4          # pattern grid resolution = 1/16 notes
BEATS_PER_BAR = 4
STEPS_PER_BAR = STEPS_PER_BEAT * BEATS_PER_BAR
N_TRACKS = 3                # pattern tracks on the master track

# FX lanes that every pattern has below the member / group lanes.
#   key        label           help text
FX_LANES = [
    ("all",      "All Members", "Lights every member"),
    ("pulse",    "Pulse",       "Beat-synced pump on every member"),
    ("chase",    "Chase",       "Runs left to right, one member per 1/16"),
    ("random",   "Random",      "Random members flicker every 1/16"),
    ("sparkle",  "Sparkle",     "Small glints across the design"),
    ("strobe",   "Strobe",      "Fast full-design flashes (rate in Look tab)"),
    ("flash",    "Flash Peak",  "Bloom burst in the accent colour"),
    ("blackout", "Blackout",    "Kills the background (band name + lyrics stay)"),
    ("logo",     "Band Name",   "Shows the band name layer"),
]
FX_KEYS = [k for k, _, _ in FX_LANES]

PATTERN_COLORS = [
    (255, 160, 40), (90, 200, 255), (255, 90, 120), (140, 230, 120),
    (200, 140, 255), (255, 220, 90), (90, 240, 210), (255, 130, 200),
]

DESIGNS = [
    ("blocks",  "Vertical Blocks"),
    ("shafts",  "Light Shafts"),
    ("horizon", "Horizon"),
    ("none",    "None (band name / lyrics only)"),
]


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
    lane: str            # "m:<member id>", "g:<group id>" or "fx:<key>"
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
class Look:
    design: str = "blocks"
    main_rgb: tuple = (255, 168, 40)        # THE song colour, shared by every member
    accent_rgb: tuple = (255, 240, 215)     # flashes / strobes / peaks
    glow_radius: float = 26.0
    glow_intensity: float = 1.0
    idle_level: float = 0.12                # how visible the design is when nothing plays
    attack_ms: float = 30.0
    release_ms: float = 380.0
    audio_react: float = 0.35
    sensitivity: float = 0.55
    slats_per_member: int = 3
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
    look: Look = field(default_factory=Look)
    bpm: float = 120.0
    beat_offset: float = 0.0
    audio_path: str | None = None
    region: tuple | None = None             # loop / export region in seconds
    loop: bool = False

    # ---- lookups -----------------------------------------------------------
    def lanes(self) -> list:
        """[(lane key, label, kind)] in display order."""
        out = [(f"m:{m.id}", m.name, "member") for m in self.members]
        out += [(f"g:{g.id}", g.name, "group") for g in self.groups]
        out += [(f"fx:{k}", label, "logo" if k == "logo" else "fx") for k, label, _ in FX_LANES]
        return out

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
            p.notes = [n for n in p.notes if n.lane != f"m:{mid}"]

    def remove_group(self, gid: str):
        self.groups = [g for g in self.groups if g.id != gid]
        for p in self.patterns:
            p.notes = [n for n in p.notes if n.lane != f"g:{gid}"]

    def remove_pattern(self, pid: str):
        self.patterns = [p for p in self.patterns if p.id != pid]
        self.clips = [c for c in self.clips if c.pattern_id != pid]

    # ---- (de)serialisation ---------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d["version"] = 2
        return d

    @staticmethod
    def from_dict(d: dict) -> "Song":
        if d.get("version") != 2:
            raise ValueError("This project was made with an older version of the app (no patterns).")

        def tup(v):
            return tuple(v) if isinstance(v, list) else v

        known = {f.name for f in fields(Look)}
        look = Look(**{k: tup(v) for k, v in d.get("look", {}).items() if k in known})
        return Song(
            members=[Member(**m) for m in d.get("members", [])],
            groups=[Group(**g) for g in d.get("groups", [])],
            patterns=[Pattern(id=p["id"], name=p["name"], bars=p.get("bars", 1), color=tup(p.get("color", (255, 160, 40))),
                              notes=[Note(**n) for n in p.get("notes", [])]) for p in d.get("patterns", [])],
            clips=[Clip(**c) for c in d.get("clips", [])],
            lyrics=[LyricClip(**l) for l in d.get("lyrics", [])],
            look=look,
            bpm=d.get("bpm", 120.0),
            beat_offset=d.get("beat_offset", 0.0),
            audio_path=d.get("audio_path"),
            region=tup(d.get("region")),
            loop=bool(d.get("loop", False)),
        )


def default_song() -> Song:
    """Six members, two groups and a few starter patterns laid out on the master track."""
    members = [Member(new_id(), f"Member {i + 1}") for i in range(6)]
    ids = [m.id for m in members]
    groups = [Group(new_id(), "Left side", ids[:3]), Group(new_id(), "Right side", ids[3:])]

    def m(i):
        return f"m:{ids[i]}"

    intro = Pattern(new_id(), "Intro breathe", 2, PATTERN_COLORS[0],
                    [Note("fx:all", 0, 8, 0.35), Note("fx:all", 16, 8, 0.3),
                     Note(m(2), 8, 6, 0.6), Note(m(3), 24, 6, 0.6)])
    groove = Pattern(new_id(), "Groove", 1, PATTERN_COLORS[1],
                     [Note("fx:pulse", 0, 16, 0.8)] +
                     [Note(f"g:{groups[i % 2].id}", i * 4, 2, 0.9) for i in range(4)])
    chorus = Pattern(new_id(), "Chorus", 2, PATTERN_COLORS[2],
                     [Note("fx:all", 0, 32, 0.85), Note("fx:flash", 0, 1, 1.0), Note("fx:logo", 0, 32, 1.0),
                      Note("fx:pulse", 0, 32, 0.6), Note("fx:sparkle", 16, 16, 0.7)])
    build = Pattern(new_id(), "Build + strobe", 1, PATTERN_COLORS[3],
                    [Note("fx:chase", 0, 12, 0.9), Note("fx:strobe", 12, 4, 1.0)])

    clips = [
        Clip(new_id(), intro.id, 0, 0.0, 16.0),
        Clip(new_id(), groove.id, 0, 16.0, 28.0),
        Clip(new_id(), build.id, 0, 44.0, 4.0),
        Clip(new_id(), chorus.id, 0, 48.0, 32.0),
    ]
    lyrics = [LyricClip(new_id(), "lyrics go here in the chorus", 48.0, 16.0)]
    return Song(members, groups, [intro, groove, chorus, build], clips, lyrics)
