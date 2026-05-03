import math

LAT = 36.9542
LNG = -25.0917


def get_quadkey(lat: float, lng: float, zoom: int) -> int:
    """
    Arguments:
        @lat -> The latitude of the point of the which we want to know the geotile
        @lng -> The longitude of the point of the which we want to know the geotile
        @zoom -> The desired zoom level

    Source: https://wiki.openstreetmap.org/wiki/QuadTiles

    A QuadTile is a hierarchical grid used for geo-data storage and indexing.
    The world map is divided into 4 quadrants. Then, the quadrant that  contains the point is chosen.
    This is then done recursively

    This is being packaged as a 64-bit integer, where the even bits represent the latitude of the point
    and the odd bits represent the longitude of a point.

    For each level of zoom, it is seen to what quadrant the point belongs to.
    The y coordinate of the quadrant is stored in the second-to-last bit,
    and the x coordinate of the point is stored as the last bit.

    This is then left-shifted by two bits, and done recursively.

    This allows for a max zoom level of 31, due to the usage of a signed 64-bit
    integer, meaning the most significant bit cannot be used.
    """
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


def get_tile_bounds(lat: float, lng: float, tile_zoom: int, max_zoom: int = 31):
    """
    Arguments:
        @lat -> Latitude of a point within the tile to get the bounds
        @lng -> Longitude of a point within the tile to get the bounds
        @tile_zoom -> The desired zoom level to get on the result
        @max_zoom -> The maximum zoom level used in the Eclipse Ditto
    """
    tile_qk = get_quadkey(lat, lng, tile_zoom)
    shift_bits = 2 * (max_zoom - tile_zoom)
    lower_bound = tile_qk << shift_bits
    upper_bound = (tile_qk + 1) << shift_bits

    return lower_bound, upper_bound


print(get_quadkey(LAT, LNG, 31))
print(get_tile_bounds(LAT, LNG, 7))


"""
For the usage in Eclipse Ditto, a Resource Query Language (RQL) query can be
used to filter by geotile, according to the desired zoom level obtained from
get_tile_bounds().

An example query is:

and(ge(attributes/geotile,<lower_bound>),le(attributes/geotile,<upper_bound>))


Which will search for all the geotiles within the bounds
"""
