"""Make the app icons from icons/logo_source.png (needs Pillow: pip install pillow).
    python tools/make_icons.py      -> icons/app.png, app.ico (Windows), app.icns (macOS)"""
import os
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICONS = os.path.join(HERE, "icons")
src = Image.open(os.path.join(ICONS, "logo_source.png")).convert("RGBA")

def write_ico(img, path, sizes=(16, 20, 24, 32, 40, 48, 64, 96, 128, 256)):
    """A Windows .ico the classic way: uncompressed 32-bit bitmaps for the small sizes and PNG only
    for 256 px. (Pillow writes every size as PNG; Explorer, the taskbar and the exe's embedded icon
    then often show a blank or generic icon.)"""
    import io
    import struct
    entries = []
    for s in sizes:
        im = img.resize((s, s), Image.LANCZOS).convert("RGBA")
        if s >= 256:
            buf = io.BytesIO()
            im.save(buf, "PNG")
            data = buf.getvalue()
        else:
            px = im.tobytes("raw", "BGRA")
            rows = [px[y * s * 4:(y + 1) * s * 4] for y in range(s)][::-1]       # bottom-up
            mask_row = ((s + 31) // 32) * 4                                     # AND mask: all 0
            data = (struct.pack("<IiiHHIIiiII", 40, s, s * 2, 1, 32, 0, s * s * 4 + mask_row * s, 0, 0, 0, 0)
                    + b"".join(rows) + b"\0" * (mask_row * s))
        entries.append((s, data))
    out = struct.pack("<HHH", 0, 1, len(entries))
    off = 6 + 16 * len(entries)
    for s, data in entries:
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), off)
        off += len(data)
    with open(path, "wb") as fh:
        fh.write(out + b"".join(d for _s, d in entries))


# square around the "LW" panels (the artwork is 3:2 with the letters in the middle)
w, h = src.size
side = h
cx = int(w * 0.498)
sq = src.crop((cx - side // 2, 0, cx + side // 2, side)).resize((1024, 1024), Image.LANCZOS)

# rounded tile with a small margin (the usual macOS / Windows app icon shape)
S, M, R = 1024, 64, 190
body = sq.resize((S - 2 * M, S - 2 * M), Image.LANCZOS)
mask = Image.new("L", body.size, 0)
ImageDraw.Draw(mask).rounded_rectangle((0, 0, body.size[0] - 1, body.size[1] - 1), R, fill=255)
icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(shadow).rounded_rectangle((M, M + 10, S - M, S - M + 10), R, fill=(0, 0, 0, 120))
icon.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(14)))
icon.paste(body, (M, M), mask)

icon.resize((512, 512), Image.LANCZOS).save(os.path.join(ICONS, "app.png"))
write_ico(icon, os.path.join(ICONS, "app.ico"))
icon.save(os.path.join(ICONS, "app.icns"))
print("written:", ", ".join(sorted(os.listdir(ICONS))))
