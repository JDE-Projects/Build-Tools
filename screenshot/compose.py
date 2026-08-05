#!/usr/bin/env python3
"""Tile screenshots into one image.

Usage:
    python compose.py OUT.png IN1.png IN2.png [...]         side by side
    python compose.py --stack OUT.png IN1.png IN2.png [...] stacked vertically

Images must all be the same size. That is deliberate: a mismatch means the
capture step produced something unexpected, and silently padding it would hide
the problem in a file that ends up in a README.
"""

import sys

from PIL import Image


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def main(argv: list) -> None:
    stack = False
    if argv and argv[0] == "--stack":
        stack = True
        argv = argv[1:]

    if len(argv) < 3:
        fail("usage: compose.py [--stack] OUT.png IN1.png IN2.png [...]")

    out_path, in_paths = argv[0], argv[1:]

    images = []
    for path in in_paths:
        try:
            images.append(Image.open(path).convert("RGB"))
        except OSError as exc:
            fail(f"could not read {path}: {exc}")

    sizes = {im.size for im in images}
    if len(sizes) != 1:
        detail = ", ".join(f"{p} {im.size[0]}x{im.size[1]}"
                           for p, im in zip(in_paths, images, strict=True))
        fail(f"all images must be the same size, got: {detail}")

    width, height = images[0].size
    if stack:
        canvas = Image.new("RGB", (width, height * len(images)))
        for index, im in enumerate(images):
            canvas.paste(im, (0, index * height))
    else:
        canvas = Image.new("RGB", (width * len(images), height))
        for index, im in enumerate(images):
            canvas.paste(im, (index * width, 0))

    canvas.save(out_path, "PNG", optimize=True)
    print(f"wrote {out_path} ({canvas.size[0]}x{canvas.size[1]})")


if __name__ == "__main__":
    main(sys.argv[1:])
