"""Tests for sat_wms.rendering module."""
import os

import numpy as np
from rio_tiler.models import ImageData


def test_gdal_num_threads_is_set():
    """Rendering module enables multi-threaded GDAL decompression."""
    import sat_wms.rendering  # noqa: F401

    assert os.environ.get("GDAL_NUM_THREADS") == "ALL_CPUS"


def _make_image(bands, h, w, value, mask_cols=None):
    """Helper: create an ImageData with optional masked columns."""
    data = np.full((bands, h, w), value, dtype=np.uint8)
    mask = np.zeros((bands, h, w), dtype=bool)
    if mask_cols is not None:
        mask[:, :, mask_cols] = True
    return ImageData(np.ma.MaskedArray(data, mask))


# ---------------------------------------------------------------------------
# composite_images
# ---------------------------------------------------------------------------

def test_composite_single_image_returns_same_data():
    """A single image passes through unchanged."""
    from sat_wms.rendering import composite_images

    img = _make_image(3, 4, 4, 100)
    result = composite_images([img])
    np.testing.assert_array_equal(result.array.data, img.array.data)


def test_composite_fills_gaps_from_second_image():
    """Second image fills transparent areas of the first."""
    from sat_wms.rendering import composite_images

    img1 = _make_image(3, 2, 4, 100, mask_cols=slice(2, 4))
    img2 = _make_image(3, 2, 4, 200)
    result = composite_images([img1, img2])
    np.testing.assert_array_equal(result.array.data[:, :, :2], 100)
    np.testing.assert_array_equal(result.array.data[:, :, 2:], 200)
    assert not np.any(result.array.mask)


def test_composite_preserves_first_image_priority():
    """Where the first image has data, the second does not overwrite it."""
    from sat_wms.rendering import composite_images

    img1 = _make_image(3, 2, 4, 100)
    img2 = _make_image(3, 2, 4, 200)
    result = composite_images([img1, img2])
    np.testing.assert_array_equal(result.array.data, 100)


def test_composite_skips_none_entries():
    """None entries (out-of-bounds reads) are silently ignored."""
    from sat_wms.rendering import composite_images

    img = _make_image(3, 2, 2, 100)
    result = composite_images([None, img, None])
    np.testing.assert_array_equal(result.array.data, 100)


def test_composite_all_none_returns_none():
    """A list of only None entries returns None."""
    from sat_wms.rendering import composite_images

    assert composite_images([None, None]) is None


def test_composite_empty_list_returns_none():
    """An empty image list returns None."""
    from sat_wms.rendering import composite_images

    assert composite_images([]) is None


def test_composite_stops_when_canvas_full():
    """Third image is not merged when the first two already fill the canvas."""
    from sat_wms.rendering import composite_images

    img1 = _make_image(3, 2, 4, 100, mask_cols=slice(2, 4))
    img2 = _make_image(3, 2, 4, 200)  # fills the remaining columns

    # Third image would overwrite value 200 with 50 — should NOT happen
    img3 = _make_image(3, 2, 4, 50)
    result = composite_images([img1, img2, img3])
    np.testing.assert_array_equal(result.array.data[:, :, :2], 100)
    np.testing.assert_array_equal(result.array.data[:, :, 2:], 200)


def test_composite_retains_remaining_mask():
    """Unfilled areas remain masked in the final result."""
    from sat_wms.rendering import composite_images

    img1 = _make_image(3, 2, 4, 100, mask_cols=slice(2, 4))
    img2 = _make_image(3, 2, 4, 200, mask_cols=slice(2, 4))  # same gap
    result = composite_images([img1, img2])
    # Columns 0-1 filled by img1, columns 2-3 still masked
    assert not np.any(result.array.mask[:, :, :2])
    assert np.all(result.array.mask[:, :, 2:])
