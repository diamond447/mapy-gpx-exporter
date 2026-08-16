"""Decodes Mapy.cz custom geometry strings and processes altitude data."""
import math
from typing import Any

ALPHABET = "0ABCD2EFGH4IJKLMN6OPQRST8UVWXYZ-1abcd3efgh5ijklmn7opqrst9uvwxyz."
FIVE_CHARS = 48
THREE_CHARS = 32
SCALE_FACTOR = (1 << 28) / 360.0
SCALE_FACTOR_Y = (1 << 28) / 180.0


class DecodingException(Exception):
    """Raised when geometry string decoding fails or produces invalid results."""
    pass


def encode_mapy_geometry(coords: list[tuple[float, float]]) -> list[str]:
    """Encodes a list of (lat, lon) coordinates into a list of absolute 10-char geometry chunks."""
    result = []
    for lat, lon in coords:
        x = int(round((lon + 180.0) * SCALE_FACTOR))
        y = int(round((lat + 90.0) * SCALE_FACTOR_Y))
        
        def encode_num(num: int) -> str:
            chars = [
                ALPHABET[48 + ((num >> 24) & 15)],
                ALPHABET[(num >> 18) & 63],
                ALPHABET[(num >> 12) & 63],
                ALPHABET[(num >> 6) & 63],
                ALPHABET[num & 63]
            ]
            return "".join(chars)
            
        result.append(encode_num(x) + encode_num(y))
    return result


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great circle distance in meters between two points on the earth."""
    # Radius of earth in meters
    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * \
        math.sin(delta_lambda / 2.0) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return r * c


def decode_mapy_geometry(encoded: str) -> list[tuple[float, float]]:
    """Decode Mapy.cz geometry string into a list of (lat, lon) coordinates.
    
    The geometry string encodes coordinate deltas, but absolute coordinates 
    are embedded at the start of chunks.
    """
    if not encoded:
        return []

    coords = [0, 0]
    coord_index = 0
    results: list[tuple[float, float]] = []
    
    index = 0
    length = len(encoded)

    def parse_number(count: int) -> int:
        nonlocal index
        result = 0
        while count > 0:
            if index >= length:
                raise DecodingException("No data!")
            ch = encoded[index]
            index += 1
            idx = ALPHABET.find(ch)
            if idx == -1:
                continue
            result <<= 6
            result += idx
            count -= 1
        return result

    try:
        while index < length:
            num = parse_number(1)
            
            if (num & FIVE_CHARS) == FIVE_CHARS:
                num -= FIVE_CHARS
                num = ((num & 15) << 24) + parse_number(4)
                coords[coord_index] = num
            elif (num & THREE_CHARS) == THREE_CHARS:
                num = ((num & 15) << 12) + parse_number(2)
                num -= 1 << 15
                coords[coord_index] += num
            else:
                num = ((num & 31) << 6) + parse_number(1)
                num -= 1 << 10
                coords[coord_index] += num
                
            if coord_index == 1:
                x = coords[0] / SCALE_FACTOR - 180.0
                y = coords[1] / SCALE_FACTOR_Y - 90.0
                
                # Sanity check: ensure the distance to the previous point is not absurdly large
                # (e.g. >20km jump is a clear sign of decoding corruption in standard routes)
                if results:
                    prev_lat, prev_lon = results[-1]
                    dist = haversine_distance(prev_lat, prev_lon, y, x)
                    if dist > 20_000:  # 20 km
                        raise DecodingException(f"Absurd distance jump detected: {dist/1000:.1f} km")
                        
                results.append((y, x))
                
            coord_index = (coord_index + 1) % 2
            
    except Exception as e:
        if isinstance(e, EOFError):
            return results
        if not isinstance(e, DecodingException):
            raise DecodingException(f"Failed to decode Mapy.cz geometry: {e}") from e
        raise
        
    return results


def interpolate_elevation(
    points: list[tuple[float, float]], 
    szn_altitude: list[dict[str, Any]],
    total_length: float | None = None
) -> list[tuple[float, float, float]]:
    """Interpolate altitude data onto the high-resolution geometry points.
    
    Mapy.cz provides exactly 100 elevation points in `sznAltitude`, containing `dist` 
    (distance from start) and `alt` (elevation). We compute cumulative distance for 
    the decoded geometry points and linearly interpolate the altitude.
    """
    if not points:
        return []

    # Sanity check: detect cumulative drift
    if total_length is not None and len(points) > 1:
        computed_length = sum(
            haversine_distance(points[i][0], points[i][1], points[i+1][0], points[i+1][1])
            for i in range(len(points) - 1)
        )
        if total_length > 0 and abs(computed_length - total_length) / total_length > 0.02:  # 2% tolerance
            raise DecodingException(
                f"Cumulative drift detected: computed length {computed_length:.1f}m differs "
                f"by >2% from FRPC totalLength {total_length:.1f}m."
            )

    if not isinstance(szn_altitude, list) or not szn_altitude:
        # Fallback to 0 elevation if altitude data is missing
        return [(lat, lon, 0.0) for lat, lon in points]
        
    # `dist` in szn_altitude is the delta distance from the previous point!
    # We must compute cumulative distances for the altitude data without sorting.
    alt_data = szn_altitude
    alt_cum_dists = [0.0] * len(alt_data)
    for i in range(1, len(alt_data)):
        alt_cum_dists[i] = alt_cum_dists[i-1] + float(alt_data[i].get("dist", 0.0))
    
    # Pre-calculate cumulative distances for the high-res track
    cum_dists = [0.0]
    for i in range(1, len(points)):
        dist = haversine_distance(points[i-1][0], points[i-1][1], points[i][0], points[i][1])
        cum_dists.append(cum_dists[-1] + dist)
        
    result_points = []
    alt_idx = 0
    max_alt_idx = len(alt_data) - 1
    
    for i, (lat, lon) in enumerate(points):
        track_dist = cum_dists[i]
        
        # Advance altitude index until we bracket the current track distance
        while alt_idx < max_alt_idx and alt_cum_dists[alt_idx + 1] < track_dist:
            alt_idx += 1
            
        if alt_idx == max_alt_idx or track_dist <= alt_cum_dists[0]:
            # We are past the last altitude point, or before the first
            alt = float(alt_data[alt_idx].get("alt", 0.0))
        else:
            # Linear interpolation
            d1 = alt_cum_dists[alt_idx]
            d2 = alt_cum_dists[alt_idx + 1]
            a1 = float(alt_data[alt_idx].get("alt", 0.0))
            a2 = float(alt_data[alt_idx + 1].get("alt", 0.0))
            
            if d2 == d1:
                alt = a1
            else:
                fraction = (track_dist - d1) / (d2 - d1)
                alt = a1 + fraction * (a2 - a1)
                
        result_points.append((lat, lon, alt))
        
    return result_points
