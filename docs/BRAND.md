# Brand and app icon

## The icon

![The PocketBooks app icon](../ios/AudiobookApp/Assets.xcassets/AppIcon.appiconset/icon_1024.png)

A kangaroo joey with over-ear headphones, peeking out of a pouch that holds a
small stack of books, on deep plum with coral sound waves. The pouch is the
"pocket" in the name. It is the sibling of the KokoroBooks icon: same flat
vector language, its own character and colourway, so the two apps read as a
pair without looking like the same product.

| | |
|---|---|
| Master file | [`ios/AudiobookApp/Assets.xcassets/AppIcon.appiconset/icon_1024.png`](../ios/AudiobookApp/Assets.xcassets/AppIcon.appiconset/icon_1024.png) |
| Size | 1024 × 1024, sRGB, **no alpha channel** |
| Shape | Full-bleed square, **no rounded corners** — iOS applies the mask |
| Web copies | [`web/assets/icon-512.png`](../web/assets/icon-512.png), [`icon-180.png`](../web/assets/icon-180.png) |

## Palette

| Role | Hex | Used for |
|---|---|---|
| Plum | `#7D254D` | The icon field |
| Plum shadow | `#45152B` | The icon's long shadow |
| Coral | `#FF7F66` | Sound waves and the pouch |
| Navy | `#0E1A2F` | Science-fiction sample covers |
| Mint teal | `#5EEAD4` | Science-fiction cover accent |
| Amber | `#E8B059` | Spy-thriller cover accent |
| Spy background | `#241610` | Spy-thriller sample covers |
| iOS blue | `#0A84FF` | The app's interactive tint |

## Regenerating it

The artwork was generated with MiniMax through the `mmx` CLI. The model insists
on drawing a white border with pre-rounded corners, which an iOS icon must not
have, so the result needs a cleanup pass.

```bash
mmx image generate --width 1024 --height 1024 --n 4 \
  --out-dir ./candidates --out-prefix kang --quiet \
  --prompt "Flat vector iOS app icon. A cute friendly kangaroo joey with big
over-ear headphones, eyes closed in a contented smile, peeking out of a pouch
that holds a small stack of three closed books. Background is a solid deep warm
plum purple filling the entire square right to the edges. Warm amber and coral
sound waves arc outward from both headphone cups. Bold flat shapes, thick
confident outlines, no gradients, no text, no letters. Artwork fills the whole
square edge to edge, no white border, no frame, no rounded corners, no drop
shadow outside the artwork."
```

The chosen generation is kept at
[`docs/brand/icon-source.jpg`](brand/icon-source.jpg), because image models are
not reproducible. Strip the frame with the helper, then check the result:

```bash
python backend/scripts/flatten_icon.py docs/brand/icon-source.jpg \
  ios/AudiobookApp/Assets.xcassets/AppIcon.appiconset/icon_1024.png \
  --background "#7D254D"
```

This icon has a long diagonal shadow that ends at the frame's rounded edge. The
shipped file additionally fills the bottom-right strip and corner with the
shadow colour `#45152B`. The helper's corner check therefore reports differing
corners for it, which is expected here. What App Store Connect actually rejects
is an alpha channel or a visible frame, and the shipped file has neither.

Refresh the web copies afterwards:

```bash
python - <<'PY'
from PIL import Image
src = Image.open("ios/AudiobookApp/Assets.xcassets/AppIcon.appiconset/icon_1024.png").convert("RGB")
for size, name in [(512, "web/assets/icon-512.png"), (180, "web/assets/icon-180.png")]:
    src.resize((size, size), Image.LANCZOS).save(name, optimize=True)
PY
```
