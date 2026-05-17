#
# geotile.py
# QuadTree-based geo indexing used to filter Ditto things by location.
#
# A QuadTile recursively splits the world into 4 quadrants. Each level adds
# 2 bits to the key (1 bit for latitude side, 1 bit for longitude side),
# packed into a signed 64-bit integer (so the sign bit is unusable -> max zoom 31).
#
# To find every thing inside a tile at a given zoom Z, compute the tile's
# zoom-Z quadkey, left-shift it by 2 * (max_zoom - Z) bits, and use the
# pair (lower_bound, lower_bound + 1<<shift) as a prefix range. In Ditto:
#
#     and(ge(attributes/geotile,<lower>),le(attributes/geotile,<upper>))
#
# Source: https://wiki.openstreetmap.org/wiki/QuadTiles
#
import math

MAX_ZOOM = 31


def get_quadkey(lat: float, lng: float, zoom: int) -> int:
    """Return the integer quadkey for (lat, lng) at the given zoom level."""
    x = int((lng + 180) / 360 * (1 << zoom))
    y = int(
        (
            1
            - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat)))
            / math.pi
        )
        / 2
        * (1 << zoom)
    )

    quadkey = 0
    for i in range(zoom, 0, -1):
        x_bit = (x >> i) & 1
        y_bit = (y >> i) & 1
        quadkey = (quadkey << 2) | (y_bit << 1) | x_bit
    return quadkey


def get_tile_bounds(
    lat: float, lng: float, tile_zoom: int, max_zoom: int = MAX_ZOOM
) -> tuple[int, int]:
    """
    Return (lower_bound, upper_bound) for a prefix-range query over geotile values
    stored at `max_zoom`, covering the tile that contains (lat, lng) at `tile_zoom`.
    """
    tile_qk = get_quadkey(lat, lng, tile_zoom)
    shift_bits = 2 * (max_zoom - tile_zoom)
    lower_bound = tile_qk << shift_bits
    upper_bound = (tile_qk + 1) << shift_bits
    return lower_bound, upper_bound
