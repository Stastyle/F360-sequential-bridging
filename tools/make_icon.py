"""Draw the Sequential Bridge icon: a hole seen from below with the first bridge pair
(blue, spanning the pocket) and the second pair (orange, resting on the first)."""
import sys
from PIL import Image, ImageDraw

out_dir = sys.argv[1]
S = 512
img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

DARK = (60, 68, 80, 255)
FACE = (222, 226, 232, 255)
BLUE = (38, 132, 235, 255)
ORANGE = (245, 146, 38, 255)

c, r, bar, m = S // 2, 112, 78, 36
d.rounded_rectangle([m, m, S - m, S - m], radius=44, fill=FACE, outline=DARK, width=24)
inner = m + 24
# first bridges: across the whole pocket, tangent to the hole
d.rectangle([inner, c - r - bar, S - inner, c - r], fill=BLUE)
d.rectangle([inner, c + r, S - inner, c + r + bar], fill=BLUE)
# second bridges: between the first pair, tangent to the hole
d.rectangle([c - r - bar, c - r, c - r, c + r], fill=ORANGE)
d.rectangle([c + r, c - r, c + r + bar, c + r], fill=ORANGE)
# the hole
d.ellipse([c - r, c - r, c + r, c + r], fill=(255, 255, 255, 255), outline=DARK, width=22)

for name, px in (('16x16', 16), ('16x16@2x', 32), ('32x32', 32), ('32x32@2x', 64), ('64x64', 64)):
    img.resize((px, px), Image.LANCZOS).save(f'{out_dir}/{name}.png')
