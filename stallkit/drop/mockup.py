"""Compositing a design onto a mockup template with Pillow.

Print areas are stored as **fractions** of the mockup, not pixels. That one choice
buys three things: a sensible default works before anyone calibrates anything, one
calibrated rectangle covers every sibling mockup of the same dimensions, and the
compositor can be tested in CI on generated images with no assets in the repo.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageOps

try:  # Colour management needs Pillow built with littlecms; the wheels always are.
    from PIL import ImageCms
except ImportError:  # pragma: no cover - a source build without lcms2
    ImageCms = None  # type: ignore[assignment]

from ..client import UPLOADABLE_SUFFIXES
from ..errors import ValidationError

# A chest print on a folded garment shot, as fractions of the mockup. Deliberately
# conservative: too small reads as a design choice, too large reads as a bug.
DEFAULT_AREA = (0.30, 0.26, 0.40, 0.36)

# Etsy recommends the shortest side be at least 2000px so the zoom viewer works.
OUTPUT_MIN_EDGE = 2000
JPEG_QUALITY = 92

# Full-resolution colour (4:4:4). libjpeg's default halves chroma in both directions,
# which bleeds saturated line art and small lettering — the detail a print is sold on.
JPEG_OPTIONS = {"quality": JPEG_QUALITY, "optimize": True, "subsampling": 0}

# A calibration preview is looked at once and thrown away, so it is deliberately small.
# Nobody wants to wait for a 4500px JPEG to open just to see the rectangle sits too low.
PREVIEW_MAX_EDGE = 1400

# EXIF tag 274. A phone does not rotate what its sensor captured; it records which way
# up the photo goes and leaves the turning to whoever displays it. Explorer, Preview and
# Etsy all obey that tag, so a mockup the seller sees as 1200x1500 portrait can be stored
# as 1500x1200 landscape. Pillow hands over the stored pixels, so anything that draws on
# them has to apply the tag first — otherwise the render comes out a quarter turn wrong
# and the print area, a fraction of the shape the seller measured on screen, lands
# somewhere else on the garment.
ORIENTATION_TAG = 274

# Orientations 5-8 are the quarter-turn ones, where stored width is displayed height.
QUARTER_TURNED = frozenset({5, 6, 7, 8})

# Every listing image lands as a JPEG on Etsy's white product page, so white is the
# ground a transparent template has to be flattened against — any other colour reads
# as a rectangle drawn around the product. flatten_design() already made this call.
WHITE = (255, 255, 255)


@dataclass(frozen=True)
class PrintArea:
    """Where the design goes, as fractions of the mockup's width and height."""

    x: float
    y: float
    w: float
    h: float

    def __post_init__(self) -> None:
        for name, value in (("x", self.x), ("y", self.y), ("w", self.w), ("h", self.h)):
            if not 0.0 <= value <= 1.0:
                raise ValidationError(f"print area {name}={value} must be between 0 and 1")
        if self.x + self.w > 1.0001 or self.y + self.h > 1.0001:
            raise ValidationError("print area extends past the edge of the mockup")
        if self.w <= 0 or self.h <= 0:
            raise ValidationError("print area has no size")

    def pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        return (
            round(self.x * width),
            round(self.y * height),
            max(1, round(self.w * width)),
            max(1, round(self.h * height)),
        )

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


DEFAULT_PRINT_AREA = PrintArea(*DEFAULT_AREA)


def parse_area(text: str) -> PrintArea:
    """Read an `x,y,w,h` string typed on the command line.

    Fractions are what get stored, so fractions are what the flag takes. Whole numbers
    are the predictable mistake — they are what a ruler in an image editor shows — and
    they get one message that says so, rather than four separate range errors.
    """
    parts = [part.strip() for part in str(text).split(",")]
    if len(parts) != 4:
        raise ValidationError(
            f"--area needs four numbers, x,y,w,h — got {text!r}. "
            f"Example: --area {','.join(format(value, 'g') for value in DEFAULT_AREA)}"
        )
    try:
        values = [float(part) for part in parts]
    except ValueError as exc:
        raise ValidationError(f"--area has a value that is not a number: {text!r}") from exc
    if any(value > 1.0 for value in values):
        raise ValidationError(
            f"--area takes fractions of the mockup between 0 and 1, not pixels: {text!r}. "
            "Divide each measurement by the mockup's width or height — or, if you already "
            "have a file of pixel rectangles, bring it over with --import."
        )
    return PrintArea(*values)


def load_positions(path: Path) -> dict[str, PrintArea]:
    """Read positions.json. A missing file is normal — defaults cover it."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{path} is not valid JSON: {exc}") from exc
    out: dict[str, PrintArea] = {}
    for name, value in (raw or {}).items():
        if not isinstance(value, dict):
            continue
        try:
            out[name] = PrintArea(
                float(value["x"]), float(value["y"]), float(value["w"]), float(value["h"])
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError(f"{path}: entry {name!r} is malformed ({exc})") from exc
    return out


def save_positions(path: Path, positions: dict[str, PrintArea]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {name: area.to_dict() for name, area in sorted(positions.items())}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_legacy_positions(path: Path) -> dict[str, Any]:
    """Read an older pixel-based mockup-positions.json, ready for conversion."""
    if not path.exists():
        raise ValidationError(f"No such file: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{path} is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise ValidationError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValidationError(f"{path} should be an object keyed by mockup filename.")
    return raw


def import_pixel_positions(
    legacy: dict[str, Any],
    mockups: dict[str, tuple[int, int]],
    *,
    on_skip: Callable[[str, str], None] | None = None,
) -> dict[str, PrintArea]:
    """Convert an older pixel-based mockup-positions.json into fractions.

    Pixel rectangles only describe the one file they were measured on. Converting to
    fractions makes a single calibration cover every sibling mockup of the same
    dimensions, which is usually most of a set.

    Every entry that cannot be converted is dropped rather than raised, including a
    rectangle that overflows the mockup it names — a legacy file is nearly always part
    rubbish, and one stale measurement must not cost the seller the twenty good ones
    beside it. `on_skip` is handed the name and the reason so the caller can say out
    loud what was left behind instead of losing it silently.
    """

    def skip(name: str, reason: str) -> None:
        if on_skip:
            on_skip(name, reason)

    out: dict[str, PrintArea] = {}
    for name, value in (legacy or {}).items():
        if not isinstance(value, dict):
            skip(name, "not a rectangle")
            continue
        if name not in mockups:
            skip(name, "no mockup with that filename")
            continue
        width, height = mockups[name]
        if not width or not height:
            skip(name, "the mockup has no readable size")
            continue
        try:
            x = float(value.get("x", value.get("left", 0)))
            y = float(value.get("y", value.get("top", 0)))
            w = float(value.get("w", value.get("width", 0)))
            h = float(value.get("h", value.get("height", 0)))
        except (TypeError, ValueError):
            skip(name, "x/y/w/h are not numbers")
            continue
        if w <= 0 or h <= 0:
            skip(name, "the rectangle has no size")
            continue
        try:
            # Values already in 0..1 were fractions to begin with.
            if max(x + w, y + h) <= 1.0:
                out[name] = PrintArea(x, y, w, h)
            else:
                out[name] = PrintArea(x / width, y / height, w / width, h / height)
        except ValidationError as exc:
            skip(name, str(exc))
    return out


def _upright(image: Image.Image) -> Image.Image:
    """Return the image the way a viewer shows it, with its EXIF block left behind.

    Pillow writes only the EXIF a caller hands to ``save(exif=...)``, so the renders
    already come out clean — but a mockup shot on a phone carries GPS, and an image
    object still holding that block is one ``exif=`` away from publishing the seller's
    home address. Dropping it at the door makes that leak impossible, not unlikely.
    """
    upright = ImageOps.exif_transpose(image)
    upright.info.pop("exif", None)
    return upright


# Integer and float greyscale modes. Pillow clamps them to 0-255 on conversion rather
# than rescaling, so a 16-bit grey mockup at mid-grey comes out as a blank white frame.
_WIDE_GREY = ("I", "I;16", "I;16B", "I;16L", "I;16N", "F")


def _to_8bit(image: Image.Image) -> Image.Image:
    """Rescale 16/32-bit or float greyscale into 8 bits on its own scale, not clip it.

    The scale is the format's, not the image's brightest pixel: stretching to the
    maximum would turn a flat mid-grey backdrop white just as surely as clipping did.
    A wide file whose values already sit inside 0-255 is taken as 8-bit data.
    """
    if image.mode not in _WIDE_GREY:
        return image
    wide = image.convert("F") if image.mode == "F" else image.convert("I").convert("F")
    high = wide.getextrema()[1]
    if image.mode == "F" and high <= 1.0:
        scale = 255.0
    elif high <= 255:
        scale = 1.0
    elif high <= 65535:
        scale = 255 / 65535
    else:
        scale = 255 / high
    return wide.point(lambda v: v * scale).convert("L")


_SRGB = ImageCms.createProfile("sRGB") if ImageCms is not None else None


def _to_srgb(image: Image.Image) -> Image.Image:
    """Convert an image carrying an ICC profile into sRGB, then drop the profile.

    The JPEGs written here carry no profile, which every browser reads as sRGB. A
    Display P3 photo from an iPhone or a CMYK print file taken at face value therefore
    comes out dull or shifted. A profile that cannot be read is ignored rather than
    fatal — the image is still usable, just unmanaged, as it was before.
    """
    icc = image.info.get("icc_profile")
    if ImageCms is None or not icc or image.mode not in ("RGB", "RGBA", "CMYK", "L"):
        return image
    out_mode = "RGBA" if image.mode == "RGBA" else "RGB"
    try:
        source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        converted = ImageCms.profileToProfile(image, source, _SRGB, outputMode=out_mode)
    except (ImageCms.PyCMSError, OSError, ValueError):
        return image
    if converted is None:
        return image
    converted.info.pop("icc_profile", None)
    return converted


def _as_displayed(image: Image.Image) -> Image.Image:
    """The image a viewer would show: turned upright, in 8 bits, in sRGB."""
    return _to_srgb(_to_8bit(_upright(image)))


def _display_size(image: Image.Image) -> tuple[int, int]:
    """The size a viewer reports, which is what a calibrated rectangle was measured on.

    Reading the tag is enough here. Decoding the pixels only to learn their shape would
    cost a full load per file, in a folder that can hold dozens of mockups.
    """
    if image.getexif().get(ORIENTATION_TAG) in QUARTER_TURNED:
        return image.height, image.width
    return image.size


def mockup_sizes(paths: list[Path]) -> dict[str, tuple[int, int]]:
    sizes: dict[str, tuple[int, int]] = {}
    for path in paths:
        try:
            with Image.open(path) as img:
                sizes[path.name] = _display_size(img)
        except (OSError, Image.DecompressionBombError):
            continue
    return sizes


def _why(exc: Exception) -> str:
    """Pillow's bomb guard says only that a limit was passed, never which one or how."""
    if isinstance(exc, Image.DecompressionBombError):
        return (
            f"{exc}. This is Pillow's decompression-bomb guard, not a corrupt file — a "
            f"very large print file can trip it. Downscale the design, or raise "
            f"PIL.Image.MAX_IMAGE_PIXELS if you trust the source"
        )
    return str(exc)


def _has_transparency(image: Image.Image) -> bool:
    """Alpha lives in a band for RGBA/LA/PA, and in info for palette and greyscale files."""
    return image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info


def _is_actually_transparent(image: Image.Image) -> bool:
    """Whether any pixel is see-through, not merely whether a channel exists.

    Canva, Figma and Photoshop's "Export As > PNG" all write an alpha channel on a
    fully opaque composition, so the channel's presence classifies a finished product
    photo as artwork and pastes the whole photo into a t-shirt. What the caller means
    by "artwork" is a file with something to see through, so that is what gets checked.
    """
    if not _has_transparency(image):
        return False
    if image.mode in ("RGBA", "LA", "PA"):
        return image.getchannel("A").getextrema()[0] < 255
    # A palette or greyscale file declares one index transparent; it counts only if the
    # image actually uses it.
    index = image.info.get("transparency")
    if isinstance(index, bytes):
        return any(alpha < 255 for alpha in index)
    if isinstance(index, int):
        low, high = image.convert("P").getextrema() if image.mode != "P" else image.getextrema()
        return low <= index <= high
    return True


def flatten_onto(image: Image.Image, background: tuple[int, int, int]) -> Image.Image:
    """Return `image` as RGB, compositing whatever transparency it has onto `background`.

    Pillow's RGBA->RGB conversion drops the alpha channel and keeps the colour hiding
    underneath it, which in a cut-out template is the black the exporter left there.
    Converting such a mockup straight to RGB therefore puts every image of the listing
    on a black background with nothing to show for it, so the transparency has to be
    composited away before the mode changes rather than by changing the mode.
    """
    # A photograph has no transparency to composite, and converting it is enough.
    if _has_transparency(image):
        ground = Image.new("RGBA", image.size, (*background, 255))
        return Image.alpha_composite(ground, image.convert("RGBA")).convert("RGB")
    return image.convert("RGB")


def compose(
    design_path: Path,
    mockup_path: Path,
    out_path: Path,
    *,
    area: PrintArea | None = None,
    min_edge: int = OUTPUT_MIN_EDGE,
    background: tuple[int, int, int] = WHITE,
) -> Path:
    """Place one design inside one mockup's print area and write a JPEG.

    The design keeps its aspect ratio and is centred in the rectangle, so a square
    print area and a wide design produce letterboxing rather than distortion. A
    template with a transparent ground — which is most cut-out shirt and mug shots —
    is flattened onto `background` first, because a JPEG cannot carry the alpha and
    dropping it would leave the product sitting on black.
    """
    area = area or DEFAULT_PRINT_AREA
    try:
        with Image.open(mockup_path) as raw_mockup:
            mockup = flatten_onto(_as_displayed(raw_mockup), background)
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValidationError(f"Cannot open mockup {mockup_path.name}: {_why(exc)}") from exc

    try:
        with Image.open(design_path) as raw_design:
            design = _as_displayed(raw_design).convert("RGBA")
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValidationError(
            f"Cannot open design {design_path.name}: {_why(exc)}. "
            "If this is an iPhone HEIC photo, install the extra: pip install stallkit[heic]"
        ) from exc

    # Enlarge the mockup BEFORE the print box is measured, so the design is resampled
    # once, straight from the source, into final output coordinates. Compositing first
    # and enlarging afterwards would run a 4500px design down to the print box on a
    # small template and then blow the result back up — the second resize cannot
    # recreate detail the first one discarded, and the artwork is what buyers zoom into.
    if min_edge and min(mockup.size) < min_edge:
        factor = min_edge / min(mockup.size)
        mockup = mockup.resize(
            (round(mockup.width * factor), round(mockup.height * factor)), Image.LANCZOS
        )

    box_x, box_y, box_w, box_h = area.pixels(*mockup.size)
    scale = min(box_w / design.width, box_h / design.height)
    new_size = (max(1, round(design.width * scale)), max(1, round(design.height * scale)))
    design = design.resize(new_size, Image.LANCZOS)

    offset = (box_x + (box_w - new_size[0]) // 2, box_y + (box_h - new_size[1]) // 2)
    mockup.paste(design, offset, design)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    mockup.save(out_path, "JPEG", **JPEG_OPTIONS)
    return out_path


def draw_preview(
    mockup_path: Path,
    out_path: Path,
    area: PrintArea,
    *,
    max_edge: int = PREVIEW_MAX_EDGE,
    background: tuple[int, int, int] = WHITE,
) -> Path:
    """Write a copy of the mockup with the print area drawn on it.

    This is the calibration interface. There is no GUI here, and four fractions tell
    nobody where a design will actually land on a photograph of a shirt — so the seller
    looks at this file, and the numbers only reach positions.json once it looks right.

    It therefore has to go through the same door compose() does. Turning the mockup
    upright and flattening it onto the same ground is not cosmetic here: a rectangle
    calibrated against a preview that disagreed with the render would be measured on
    the wrong shape, which is the one way this command could make things worse.
    """
    try:
        with Image.open(mockup_path) as raw:
            preview = flatten_onto(_as_displayed(raw), background).convert("RGBA")
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValidationError(f"Cannot open mockup {mockup_path.name}: {_why(exc)}") from exc

    box_x, box_y, box_w, box_h = area.pixels(*preview.size)
    box = (box_x, box_y, box_x + box_w - 1, box_y + box_h - 1)

    # The outline is drawn twice, a dark band immediately inside a bright one, because a
    # single colour vanishes on the mockup that happens to match it. The tint fills the
    # area itself, so a rectangle that landed off the garment is obvious at a glance.
    overlay = Image.new("RGBA", preview.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    edge = max(2, round(min(preview.size) / 250))
    draw.rectangle(box, fill=(255, 64, 64, 56))
    draw.rectangle(box, outline=(0, 0, 0, 220), width=edge * 3)
    draw.rectangle(box, outline=(255, 64, 64, 255), width=edge)
    preview = Image.alpha_composite(preview, overlay).convert("RGB")

    if max_edge and max(preview.size) > max_edge:
        factor = max_edge / max(preview.size)
        preview = preview.resize(
            (max(1, round(preview.width * factor)), max(1, round(preview.height * factor))),
            Image.LANCZOS,
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(out_path, "JPEG", **JPEG_OPTIONS)
    return out_path


def flatten_design(
    design_path: Path,
    out_path: Path,
    *,
    edge: int = OUTPUT_MIN_EDGE,
    background: tuple[int, int, int] = WHITE,
) -> Path:
    """Render the artwork itself on white — the image print-on-demand buyers look for.

    Transparent artwork on Etsy's white page is invisible, so it gets a background, and
    `background` is the same knob compose() takes for shops that stage on another colour.
    """
    try:
        with Image.open(design_path) as raw:
            design = _as_displayed(raw).convert("RGBA")
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValidationError(f"Cannot open design {design_path.name}: {_why(exc)}") from exc

    canvas = Image.new("RGB", (edge, edge), background)
    scale = min(edge * 0.86 / design.width, edge * 0.86 / design.height)
    size = (max(1, round(design.width * scale)), max(1, round(design.height * scale)))
    design = design.resize(size, Image.LANCZOS)
    canvas.paste(design, ((edge - size[0]) // 2, (edge - size[1]) // 2), design)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "JPEG", **JPEG_OPTIONS)
    return out_path


def to_uploadable(photo_path: Path, out_dir: Path) -> Path:
    """Re-encode a finished photo Etsy would refuse; return anything it accepts as is.

    Ready photos are uploaded byte for byte, which is the point of that route — but
    Etsy takes only JPG, PNG and GIF, and a seller exporting from Canva or a phone
    ends up with .webp without ever choosing it. Refusing the run would send them back
    to re-export forty files; converting without saying so would hide a lossless-to-
    lossy step they never asked for. So this converts and the caller warns. The copy
    lands in the batch folder beside the composites, where it is reviewed like any
    other output before anything is pushed.
    """
    if photo_path.suffix.lower() in UPLOADABLE_SUFFIXES:
        return photo_path

    try:
        with Image.open(photo_path) as raw:
            # Re-encoding drops the EXIF block, and with it the orientation tag Etsy
            # would otherwise have honoured — so the turn has to be applied here.
            photo = _as_displayed(raw).convert("RGBA")
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValidationError(f"Cannot open photo {photo_path.name}: {_why(exc)}") from exc

    # JPEG carries no alpha, and Etsy's product page is white — so a transparent ready
    # photo goes on white, which is what the buyer would have seen from it anyway.
    canvas = Image.new("RGB", photo.size, (255, 255, 255))
    canvas.paste(photo, (0, 0), photo)

    # The original suffix stays in the name: two files in one product folder can differ
    # only by extension, and the stem is kept first so natural filename order survives.
    out_path = out_dir / f"{photo_path.stem}-{photo_path.suffix.lstrip('.').lower()}.jpg"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "JPEG", **JPEG_OPTIONS)
    return out_path


def looks_like_artwork(path: Path) -> bool:
    """Artwork needs compositing; a finished product photo does not.

    Transparency is the signal that actually means something: a photograph has none,
    and a print file almost always does. Everything else is guesswork, so when there
    is no alpha channel we treat it as a finished photo and let the seller override.

    It is the see-through pixels that decide, not the channel: an opaque RGBA export
    is a photo, however it was saved.
    """
    try:
        with Image.open(path) as img:
            return _is_actually_transparent(img)
    except (OSError, Image.DecompressionBombError):
        return False
