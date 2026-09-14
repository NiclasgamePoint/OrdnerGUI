"""Export native icon containers from the approved transparent PNG artwork.

Run with a development interpreter containing Pillow. No artwork is generated
or edited here; PNG alpha and the original source files are preserved.
"""

from pathlib import Path

from PIL import Image


ICON_DIRECTORY = (
    Path(__file__).resolve().parents[1]
    / "packages/client/src/papagui_client/resources/icons"
)
WINDOWS_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def main() -> None:
    for role in ("client", "server"):
        source = ICON_DIRECTORY / f"papagui-{role}.png"
        with Image.open(source) as artwork:
            if artwork.mode != "RGBA" or artwork.width != artwork.height:
                raise ValueError(f"Expected square RGBA artwork: {source.name}")
            if artwork.getchannel("A").getextrema() != (0, 255):
                raise ValueError(f"Expected transparent background: {source.name}")
            artwork.save(source.with_suffix(".ico"), sizes=[(n, n) for n in WINDOWS_SIZES])
            artwork.save(source.with_suffix(".icns"))
        print(f"Exported {source.stem}: ICO and ICNS")


if __name__ == "__main__":
    main()
