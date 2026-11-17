"""Focus View backgrounds: fit a scene plate to the Floor screen, composite gear, preview in RGB555.

Screen facts (hx-unbound-floor FINDINGS, 2026-11-13/14): 800 x 480, RGB555 (R << 10 | G << 5 | B),
Focus View picture box 256 x 256 at x 272..528, y 68..324; stock title bar above (~y 0..56) and the
parameter area below (orange line ~y 349, rows to 480). Both stock areas are kept pure black here so
the stock widgets, which paint on black, sit on the background without a seam.

Pillow only (no numpy). Usage: python focus_bg.py samples
"""
import json
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageChops

HERE = Path(__file__).resolve().parent
ICONS = Path(os.environ.get("HELIX_ICONS", HERE.parent / "helix_model_icons"))
PLATES = Path(os.environ.get("FOCUS_PLATES", HERE / "plates"))
W, H = 800, 480
BOX = (272, 68, 528, 324)          # Focus View picture box (256 x 256)
TOP_BLACK, TOP_FADE = 52, 96       # pure black above TOP_BLACK, fade in until TOP_FADE
BOT_FADE, BOT_BLACK = 300, 342     # fade out from BOT_FADE, pure black from BOT_BLACK

# Category light tints (close to the Floor's block colours).
TINT = {
    "amp": (232, 64, 52), "cab": (200, 70, 50), "preamp": (220, 90, 60), "distortion": (240, 160, 40),
    "dynamics": (235, 205, 60), "eq": (225, 215, 110), "modulation": (60, 150, 240),
    "delay": (70, 210, 110), "reverb": (240, 120, 40), "pitch": (170, 90, 230), "filter": (160, 90, 220),
    "wah": (150, 100, 220), "volume": (60, 200, 190), "neutral": (220, 220, 220),
}

FONT_DIR = Path(r"C:\Windows\Fonts")


def font(name, size):
    return ImageFont.truetype(str(FONT_DIR / name), size)


def fit_plate(plate, focus=(0.5, 0.56), zoom=1.0):
    """Scale/crop a plate to 800 x 480 so its point `focus` (fractions) lands under the gear's base."""
    p = plate.convert("RGB")
    s = max(W / p.width, H / p.height) * zoom
    p = p.resize((round(p.width * s), round(p.height * s)), Image.LANCZOS)
    fx, fy = focus[0] * p.width, focus[1] * p.height
    tx, ty = 400, 300                      # where the focus point should land on screen
    x0 = int(min(max(fx - tx, 0), p.width - W))
    y0 = int(min(max(fy - ty, 0), p.height - H))
    return p.crop((x0, y0, x0 + W, y0 + H))


def zone_mask():
    """L mask: 255 where the scene shows, 0 where the screen must stay black."""
    m = Image.new("L", (W, H), 0)
    px = m.load()
    for y in range(H):
        if y < TOP_BLACK or y >= BOT_BLACK:
            v = 0
        elif y < TOP_FADE:
            t = (y - TOP_BLACK) / (TOP_FADE - TOP_BLACK)
            v = int(255 * t * t)
        elif y > BOT_FADE:
            t = (BOT_BLACK - y) / (BOT_BLACK - BOT_FADE)
            v = int(255 * t * t)
        else:
            v = 255
        for x in range(W):
            px[x, y] = v
    # soft side vignette
    vg = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vg).ellipse((-120, -60, W + 120, H + 160), fill=255)
    vg = vg.filter(ImageFilter.GaussianBlur(90))
    return ImageChops.multiply(m, vg)


_MASK = None


def background(plate, category="neutral", tint_amount=0.16, gain=0.9, focus=(0.5, 0.56), zoom=1.0):
    global _MASK
    if _MASK is None:
        _MASK = zone_mask()
    bg = fit_plate(plate, focus, zoom)
    bg = Image.eval(bg, lambda v: int(v * gain))
    # tint the light pool (bright parts) with the category colour
    col = Image.new("RGB", (W, H), TINT.get(category, TINT["neutral"]))
    lum = bg.convert("L").point(lambda v: int(min(255, v * 2.2) * tint_amount))
    tinted = ImageChops.multiply(bg, col)
    tinted = Image.blend(bg, ImageChops.screen(tinted, bg), 0.5)
    bg = Image.composite(tinted, bg, lum)
    black = Image.new("RGB", (W, H), 0)
    return Image.composite(bg, black, _MASK)


def gear_fit(path):
    """Gear render scaled into the 256 x 256 box exactly as the eMMC store holds it (bottom-centred)."""
    g = Image.open(path).convert("RGBA")
    g = g.crop(g.getbbox())
    s = min(248 / g.width, 248 / g.height)
    g = g.resize((max(1, round(g.width * s)), max(1, round(g.height * s))), Image.LANCZOS)
    return g


def composite(bg, gear_path):
    out = bg.copy().convert("RGBA")
    g = gear_fit(gear_path)
    gx = (BOX[0] + BOX[2]) // 2 - g.width // 2
    gy = BOX[3] - 4 - g.height
    # contact shadow: a squashed, blurred copy of the gear's footprint
    sh = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(sh)
    cx, base = gx + g.width / 2, gy + g.height
    d.ellipse((cx - g.width * 0.52, base - 10, cx + g.width * 0.52, base + 9), fill=170)
    d.ellipse((cx - g.width * 0.40, base - 5, cx + g.width * 0.40, base + 4), fill=230)
    sh = sh.filter(ImageFilter.GaussianBlur(9))
    out = Image.composite(Image.new("RGBA", (W, H), (0, 0, 0, 255)), out, sh)
    out.alpha_composite(g, (gx, gy))
    return out.convert("RGB")


BAYER4 = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]


def rgb555(img, dither=True):
    """Round-trip through the Floor's RGB555 (5 bits per channel), ordered dither like the store would use."""
    img = img.convert("RGB")
    src = img.load()
    out = Image.new("RGB", img.size)
    dst = out.load()
    for y in range(img.height):
        row = BAYER4[y & 3]
        for x in range(img.width):
            r, g, b = src[x, y]
            o = (row[x & 3] / 2 - 4) if dither else 0     # -4..+3.5: half a 5-bit step
            q = []
            for v in (r, g, b):
                v5 = max(0, min(31, int((v + o + 4) / 8.226)))
                q.append((v5 << 3) | (v5 >> 2))
            dst[x, y] = tuple(q)
    return out


def to_rgb555_bytes(img):
    """Little-endian RGB555 rows from the top, as Entry 9 / the eMMC store hold pixels."""
    img = img.convert("RGB")
    b = bytearray()
    for r, g, bl in img.getdata():
        v = (r >> 3) << 10 | (g >> 3) << 5 | (bl >> 3)
        b += v.to_bytes(2, "little")
    return bytes(b)


def mock_ui(img, preset="01B", title="Essex A30", cat="Amp", model="Essex A30", accent=(240, 110, 40),
            params=(("5.0", "Drive"), ("4.2", "Bass"), ("6.0", "Treble"), ("3.5", "Cut"), ("50%", "Master"), ("0.0dB", "Level"))):
    """Draw a stand-in for the stock UI (anti-aliased title treatment) to judge legibility."""
    im = img.copy()
    d = ImageDraw.Draw(im)
    d.text((8, 2), preset, font=font("DejaVuSansCondensed-Bold.ttf", 40), fill="white")
    pw = d.textlength(preset, font=font("DejaVuSansCondensed-Bold.ttf", 40))
    d.text((8 + pw + 14, 2), title, font=font("DejaVuSansCondensed.ttf", 40), fill="white")
    # path meters + snapshot box
    small = font("DejaVuSansCondensed-Bold.ttf", 11)
    d.text((578, 15), "P1", font=small, fill="white"); d.rectangle((596, 16, 650, 24), outline="white"); d.rectangle((597, 17, 649, 23), fill=(230, 20, 20))
    d.text((660, 15), "P2", font=small, fill="white"); d.rectangle((678, 16, 732, 24), outline="white")
    d.rounded_rectangle((744, 6, 792, 44), 6, outline="white", width=2)
    d.text((761, 9), "1", font=font("DejaVuSansCondensed-Bold.ttf", 26), fill="white")
    # parameter area
    d.line((0, 349, 400, 349), fill=accent, width=2)
    d.line((400, 349, 800, 349), fill=(70, 70, 70), width=1)
    f22 = font("DejaVuSansCondensed.ttf", 22)
    d.text((44, 360), cat, font=font("DejaVuSansCondensed-Bold.ttf", 22), fill=accent)
    cw = d.textlength(cat + " ", font=font("DejaVuSansCondensed-Bold.ttf", 22))
    d.text((44 + cw, 360), model, font=f22, fill="white")
    d.rectangle((14, 364, 32, 382), outline=accent, width=2)
    f18 = font("DejaVuSansCondensed.ttf", 18)
    for i, (val, name) in enumerate(params):
        if not name:
            continue
        cx = 66 + i * 134
        d.text((cx, 397), val, font=f18, fill=accent, anchor="mt")
        d.line((cx - 58, 425, cx + 58, 425), fill=(90, 90, 90), width=3)
        d.line((cx - 58, 425, cx - 10, 425), fill=accent, width=3)
        d.text((cx, 438), name, font=f22, fill="white", anchor="mt")
    return im


def contact_sheet(rows, cols_labels, row_labels, cell_w=400, out=None):
    """rows: list of lists of images. Downscale to cell_w and label."""
    cell_h = round(cell_w * H / W)
    lab = font("DejaVuSansCondensed-Bold.ttf", 20)
    pad, left, top = 8, 185, 36
    sheet = Image.new("RGB", (left + len(cols_labels) * (cell_w + pad), top + len(rows) * (cell_h + pad)), (24, 24, 26))
    d = ImageDraw.Draw(sheet)
    for c, t in enumerate(cols_labels):
        d.text((left + c * (cell_w + pad) + cell_w // 2, 8), t, font=lab, fill="white", anchor="mt")
    for r, row in enumerate(rows):
        d.text((8, top + r * (cell_h + pad) + cell_h // 2), row_labels[r], font=lab, fill="white", anchor="lm")
        for c, im in enumerate(row):
            sheet.paste(im.resize((cell_w, cell_h), Image.LANCZOS), (left + c * (cell_w + pad), top + r * (cell_h + pad)))
    if out:
        sheet.save(out)
    return sheet


SAMPLE_GEAR = {
    "amp": ("full/gtr_essexa30.png", "Amp", "Essex A30", "amp",
            (("5.0", "Drive"), ("4.2", "Bass"), ("6.0", "Treble"), ("3.5", "Cut"), ("50%", "Master"), ("0.0dB", "Level"))),
    "pedal": ("full/dist_minotaur.png", "Distortion", "Minotaur", "distortion",
              (("5.0", "Gain"), ("5.0", "Tone"), ("0.0dB", "Level"), ("Off", "Bright"), ("", ""), ("", ""))),
    "reverb": ("full/rev_ganymede.png", "Reverb", "Ganymede", "reverb",
               (("5.0", "Decay"), ("80 ms", "Predelay"), ("93 Hz", "Low Cut"), ("6.6 kHz", "High Cut"), ("36%", "Mix"), ("0.0dB", "Level"))),
}

ACCENT = {"amp": (235, 60, 50), "distortion": (240, 160, 40), "reverb": (240, 110, 40)}

STYLES = {k: v for k, v in json.loads((HERE / "styles.json").read_text()).items() if not k.startswith("_")} if (HERE / "styles.json").exists() else {}


def samples(names=None, sheet="contact-sheet.png"):
    """Render the sample grid for the named styles (default: all) and one contact sheet."""
    out = HERE / "samples"
    out.mkdir(exist_ok=True)
    names = names or list(STYLES)
    grid, labels = [], []
    for sname in names:
        st = STYLES[sname]
        plate = Image.open(PLATES / st["plate"]).convert("RGB")
        if st.get("blur"):
            plate = plate.filter(ImageFilter.GaussianBlur(st["blur"]))
        row = []
        for key, (gpath, cat, model, tcat, params) in SAMPLE_GEAR.items():
            bg = background(plate, tcat, st.get("tint", 0.16), st.get("gain", 0.9), tuple(st.get("focus", (0.5, 0.56))), st.get("zoom", 1.0))
            comp = composite(bg, ICONS / gpath)
            ui = mock_ui(comp, title=model, cat=cat, model=model, accent=ACCENT[tcat], params=params)
            ui = rgb555(ui)
            ui.save(out / f"{sname}_{key}.png")
            row.append(ui)
        grid.append(row)
        labels.append(sname.replace("_", " "))
        print("done", sname, flush=True)
    contact_sheet(grid, list(SAMPLE_GEAR), labels, out=out / sheet)


CAT_LABEL = {"amp": "Amp", "distortion": "Distortion", "dynamics": "Dynamics", "modulation": "Modulation",
             "delay": "Delay", "reverb": "Reverb", "wah": "Wah", "pitch": "Pitch/Synth", "eq": "EQ", "filter": "Filter",
             "io": "I/O", "routing": "Split", "send/return": "FX Loop", "looper": "Looper", "volume": "Vol/Pan"}
CAT_ACCENT = {"amp": (235, 60, 50), "distortion": (240, 160, 40), "dynamics": (235, 205, 60), "modulation": (70, 150, 245),
              "delay": (70, 210, 110), "reverb": (240, 110, 40), "wah": (175, 100, 235), "pitch": (175, 100, 235),
              "io": (190, 196, 205), "routing": (190, 196, 205), "send/return": (190, 196, 205), "looper": (190, 196, 205),
              "volume": (60, 200, 190)}
CAT_PARAMS = {
    "amp": (("6.0", "Drive"), ("5.0", "Bass"), ("5.5", "Mid"), ("6.5", "Treble"), ("7.0", "Ch Vol"), ("5.0", "Master")),
    "distortion": (("6.5", "Gain"), ("5.0", "Tone"), ("0.0dB", "Level"), ("", ""), ("", ""), ("", "")),
    "dynamics": (("-18dB", "Threshold"), ("4:1", "Ratio"), ("10 ms", "Attack"), ("200 ms", "Release"), ("50%", "Mix"), ("0.0dB", "Level")),
    "modulation": (("1.2 Hz", "Speed"), ("60%", "Depth"), ("50%", "Mix"), ("0.0dB", "Level"), ("", ""), ("", "")),
    "delay": (("1/8", "Time"), ("35%", "Feedback"), ("30%", "Mix"), ("0.0dB", "Level"), ("", ""), ("", "")),
    "reverb": (("5.0", "Decay"), ("80 ms", "Predelay"), ("93 Hz", "Low Cut"), ("6.6 kHz", "High Cut"), ("36%", "Mix"), ("0.0dB", "Level")),
    "wah": (("50%", "Position"), ("100%", "Mix"), ("0.0dB", "Level"), ("", ""), ("", ""), ("", "")),
    "io": (("On", "Gate"), ("-48dB", "Threshold"), ("100 ms", "Decay"), ("", ""), ("", ""), ("", "")),
    "routing": (("100%", "Path A"), ("100%", "Path B"), ("Off", "Bypass"), ("", ""), ("", ""), ("", "")),
    "send/return": (("0.0dB", "Send"), ("0.0dB", "Return"), ("100%", "Mix"), ("Off", "Trails"), ("", ""), ("", "")),
    "looper": (("0.0dB", "Playback"), ("0.0dB", "Overdub"), ("Off", "Low Cut"), ("Off", "High Cut"), ("", ""), ("", "")),
}


def _render_index():
    icons = json.loads((ICONS / "icons.json").read_text())["icons"]
    return {v["render"]: v for v in icons.values()}


def pack(dest):
    """Write the shareable pack: plain backgrounds, previews, overview sheet, manifest."""
    dest = Path(dest)
    (dest / "backgrounds").mkdir(parents=True, exist_ok=True)
    (dest / "previews").mkdir(exist_ok=True)
    renders = _render_index()
    styles_out, cells, labels = [], [], []
    for sid, st in STYLES.items():
        plate = Image.open(PLATES / st["plate"]).convert("RGB")
        if st.get("blur"):
            plate = plate.filter(ImageFilter.GaussianBlur(st["blur"]))
        focus = tuple(st.get("focus", (0.5, 0.56)))
        bg = background(plate, "neutral", st.get("tint", 0.0), st.get("gain", 0.9), focus, st.get("zoom", 1.0))
        bg.save(dest / "backgrounds" / f"{sid}.png")
        r = renders[st["showcase"]]
        cat = r["category"]
        comp = composite(bg if not st.get("tint") else background(plate, cat, st["tint"], st.get("gain", 0.9), focus, st.get("zoom", 1.0)),
                         ICONS / r["full"])
        ui = mock_ui(comp, preset="01A", title=r["display_name"], cat=CAT_LABEL.get(cat, cat.title()), model=r["display_name"],
                     accent=CAT_ACCENT.get(cat, (240, 110, 40)), params=CAT_PARAMS.get(cat, CAT_PARAMS["reverb"]))
        ui = rgb555(ui)
        ui.save(dest / "previews" / f"{sid}.png")
        cells.append(ui)
        labels.append(st["name"] + ("  (locked)" if st["status"] == "locked" else ""))
        styles_out.append({
            "id": sid, "name": st["name"], "group": st.get("group", "scene"), "status": st["status"], "description": st["description"],
            "background": f"backgrounds/{sid}.png", "preview": f"previews/{sid}.png",
            "preview_gear": {"render": st["showcase"], "display_name": r["display_name"], "category": cat},
            "source": {"plate": st["plate"], "focus": list(focus), "zoom": st.get("zoom", 1.0), "gain": st.get("gain", 0.9),
                       "blur": st.get("blur", 0), "category_tint": st.get("tint", 0.0)},
        })
        print("packed", sid, flush=True)
    overview(cells, labels, dest / "contact-sheet.png", cols=5, cell_w=320)
    manifest = {
        "about": "Newly generated scene backgrounds for the Floor's Focus View (AI image renders from written scene "
                 "descriptions, framed and toned by focus_bg.py). No Line 6 artwork is included, copied or traced.",
        "screen": {"width": W, "height": H, "pixel_format": "RGB555 little-endian, R << 10 | G << 5 | B, rows from the top",
                   "gear_box": {"x": BOX[0], "y": BOX[1], "w": BOX[2] - BOX[0], "h": BOX[3] - BOX[1]},
                   "black_above_y": TOP_BLACK, "fade_in_until_y": TOP_FADE, "fade_out_from_y": BOT_FADE, "black_from_y": BOT_BLACK},
        "backgrounds_note": "backgrounds/*.png are full colour 800 x 480; dither to RGB555 when converting for the unit "
                            "(focus_bg.rgb555 / to_rgb555_bytes). previews/*.png are already RGB555-dithered and carry a "
                            "stand-in UI (anti-aliased title) to judge legibility; they are not for the unit.",
        "category_tints": {k: list(v) for k, v in TINT.items()},
        "assignment": None,
        "assignment_note": "Undecided: theme packs or one scene per category. The firmware side only needs "
                           "category -> background slot; the PC side fills the slots.",
        "styles": styles_out,
    }
    (dest / "focus_backgrounds.json").write_text(json.dumps(manifest, indent=1) + "\n")


def overview(cells, labels, out, cols=3, cell_w=400):
    cell_h = round(cell_w * H / W)
    lab = font("DejaVuSansCondensed-Bold.ttf", 18)
    pad, cap = 10, 26
    rows = (len(cells) + cols - 1) // cols
    sheet = Image.new("RGB", (pad + cols * (cell_w + pad), pad + rows * (cell_h + cap + pad)), (22, 22, 24))
    d = ImageDraw.Draw(sheet)
    for i, (im, t) in enumerate(zip(cells, labels)):
        x, y = pad + (i % cols) * (cell_w + pad), pad + (i // cols) * (cell_h + cap + pad)
        d.text((x + 2, y + 2), t, font=lab, fill="white")
        sheet.paste(im.resize((cell_w, cell_h), Image.LANCZOS), (x, y + cap))
    sheet.save(out)


if __name__ == "__main__":
    # python focus_bg.py samples [sheet.png style1 style2 ...]
    if sys.argv[1:2] == ["pack"]:
        pack(sys.argv[2])
    elif len(sys.argv) > 3:
        samples(sys.argv[3:], sys.argv[2])
    else:
        samples()
