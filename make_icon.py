"""Generate app icon: blue frame + person silhouette."""
from PIL import Image, ImageDraw, ImageFilter

def round_rect(draw, xy, r, **kw):
    x0, y0, x1, y1 = xy
    r = min(r, (x1 - x0) // 2, (y1 - y0) // 2)
    draw.rounded_rectangle([x0, y0, x1, y1], radius=r, **kw)

def draw_icon(size: int) -> Image.Image:
    s  = size
    cx = s / 2
    cy = s / 2

    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)

    # ── Background ────────────────────────────────────────────────────────────
    bg_r = max(4, int(s * 0.18))
    round_rect(d, [0, 0, s - 1, s - 1], bg_r, fill=(11, 13, 18, 255))

    # ── Glow behind frame ─────────────────────────────────────────────────────
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd   = ImageDraw.Draw(glow)
    pad  = int(s * 0.12)
    fr   = max(2, int(s * 0.10))
    round_rect(gd, [pad, pad, s - pad, s - pad], fr, fill=(67, 97, 238, 70))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=s * 0.10))
    img  = Image.alpha_composite(img, glow)
    d    = ImageDraw.Draw(img)

    # ── Blue border frame ─────────────────────────────────────────────────────
    lw = max(2, int(s * 0.065))
    round_rect(d, [pad, pad, s - pad, s - pad], fr,
               outline=(67, 97, 238, 255), width=lw)

    # ── Person silhouette (white) ─────────────────────────────────────────────
    # head
    head_r = s * 0.13
    head_cy = cy - s * 0.10
    d.ellipse([cx - head_r, head_cy - head_r,
               cx + head_r, head_cy + head_r],
              fill=(230, 237, 243, 240))

    # body / shoulders — rounded trapezoid
    body_top = head_cy + head_r - s * 0.01
    body_bot = cy + s * 0.26
    body_w_top = s * 0.18
    body_w_bot = s * 0.30
    body_pts = [
        (cx - body_w_top, body_top),
        (cx + body_w_top, body_top),
        (cx + body_w_bot, body_bot),
        (cx - body_w_bot, body_bot),
    ]
    d.polygon(body_pts, fill=(230, 237, 243, 220))

    return img


master = draw_icon(256)
master.save(
    "icon.ico", format="ICO",
    sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)],
)

# preview
master.save("icon_preview.png")
print("Saved icon.ico + icon_preview.png")
