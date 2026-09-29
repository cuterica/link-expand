"""Rank available media by resolution, frame rate, then bitrate."""

import re
from fractions import Fraction


def quality_rank(variant):
    width, height = variant.get('width'), variant.get('height')
    dimensions = re.search(r'(\d+)\s*[x×]\s*(\d+)', str(variant.get('quality') or '') + ' ' + str(variant.get('url') or ''))
    try:
        width = int(width or (dimensions[1] if dimensions else 0))
        height = int(height or (dimensions[2] if dimensions else 0))
        rate = float(Fraction(str(variant.get('frame_rate') or variant.get('frameRate') or 0)))
        bitrate = int(variant.get('bitrate') or variant.get('bandwidth') or 0)
        return width * height, height, rate, bitrate
    except (ValueError, TypeError, ZeroDivisionError):
        return 0, 0, 0, 0
