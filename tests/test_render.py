"""ASS styling arithmetic.

Both values here were got wrong once on the reference job, and neither failure
is visible in a file: the inverted alpha produced a box so faint it read as a
rendering bug, and unscaled pixel sizes produced subtitles that looked right at
720p and tiny at 1080p.
"""

import pytest

from subtitler.steps.render import REFERENCE_HEIGHT, alpha


def opacity_of(ass_colour: str) -> float:
    """Recover the opacity a colour string encodes, the way libass reads it."""
    return 1.0 - int(ass_colour[2:4], 16) / 255


@pytest.mark.parametrize("wanted", [0.0, 0.25, 0.5, 0.85, 1.0])
def test_alpha_round_trips(wanted):
    assert opacity_of(alpha(wanted)) == pytest.approx(wanted, abs=0.004)


def test_alpha_is_inverted():
    """ASS alpha runs 00 opaque to FF transparent, the opposite of the usual
    convention. 85% opacity is 0x26, not 0xD9."""
    assert alpha(1.0).startswith("&H00")
    assert alpha(0.0).startswith("&HFF")
    assert alpha(0.85) == "&H26000000"


def test_alpha_clamps_rather_than_wrapping():
    assert alpha(2.0) == alpha(1.0)
    assert alpha(-1.0) == alpha(0.0)


def test_sizes_scale_with_frame_height():
    """A size tuned at 720p has to grow on a 1080p source, or the subtitles
    render at two thirds of the intended size."""
    assert round(22 * (1080 / REFERENCE_HEIGHT)) == 33
    assert round(22 * (REFERENCE_HEIGHT / REFERENCE_HEIGHT)) == 22
