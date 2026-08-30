"""Groups frames that have GPS by the place they were shot.

Why this is kept separate from the time (scene) groups
------------------------------------------------------
A group in `grouping.py` is a **burst** - 3~10 similar frames within 3
seconds make one group, and its purpose is to pick the best out of them. A
place is a completely different animal. Go round three spots in a day and
there are 3 places but hundreds of scenes.

So it is kept as a separate axis. It is used to split into per-place
folders on export, or to view "this place only" in the grid.

On accuracy
-----------
It does not attach neighbourhood names. That would need online geocoding,
which means sending the photo's coordinates to an outside server, and that
is not this tool's job. Instead only the coordinates themselves and the
group number are produced, and the user can name the folders by looking at
them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .types import ImageRecord

DEFAULT_RADIUS_M = 250.0
"""The radius (m) within which frames count as the same place.

One venue, or one park, fits inside this. Narrow it further and phone GPS
error alone (commonly 20~50m in a city centre, larger indoors) splits a
single venue in two. Widen it and different places in the same
neighbourhood merge.
"""

MIN_CLUSTER = 2
"""Below this many frames it is not made into a place of its own but left
as 'other'.

Make a folder even for a coordinate where one frame was taken in transit
and you end up with dozens of folders.
"""

_EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """The distance (m) between two coordinates.

    A flat approximation fails to account for the real distance of one
    degree of longitude shrinking sharply at high latitudes. At 60 degrees
    of latitude the error is 2x.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    return 2.0 * _EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


@dataclass
class Place:
    """One place. The coordinates are the mean of the frames in it."""

    index: int
    latitude: float
    longitude: float
    records: list[ImageRecord] = field(default_factory=list)

    @property
    def label(self) -> str:
        """The string used as the folder name. Coordinates to 4 decimal
        places (~11m)."""
        ns = "N" if self.latitude >= 0 else "S"
        ew = "E" if self.longitude >= 0 else "W"
        return (f"{self.index:02d}_{abs(self.latitude):.4f}{ns}_"
                f"{abs(self.longitude):.4f}{ew}")


def _coordinates(record: ImageRecord) -> tuple[float, float] | None:
    meta = record.metadata
    if meta is None or not getattr(meta, "has_location", False):
        return None
    return float(meta.latitude), float(meta.longitude)


def assign_places(
    records: list[ImageRecord], radius_m: float = DEFAULT_RADIUS_M,
    min_cluster: int = MIN_CLUSTER,
) -> list[Place]:
    """Assigns a place_id to frames that have GPS and returns the list of
    places.

    A frame with no coordinates keeps place_id None - a frame shot without
    GPS must not be mixed into some arbitrary place.

    The grouping method is **sweeping in capture-time order and joining
    them up**. Photos are taken along a route, so in time order the same
    place appears in a run. Global clustering (k-means and the like) needs
    the number of places known in advance, and has the problem of one frame
    taken in transit bridging two places together.
    """
    for record in records:
        record.place_id = None

    located = [r for r in records if _coordinates(r) is not None]
    if not located:
        return []

    located.sort(key=lambda r: (
        r.metadata.capture_time or __import__("datetime").datetime.min,
        r.path.name,
    ))

    clusters: list[list[ImageRecord]] = []
    centre: tuple[float, float] | None = None
    for record in located:
        lat, lon = _coordinates(record)
        if centre is not None and haversine_m(centre[0], centre[1], lat, lon) <= radius_m:
            clusters[-1].append(record)
            # Update the centre as a running mean. Pinned to the first
            # frame, walking across a venue takes the later frames outside
            # the radius and splits them off.
            count = len(clusters[-1])
            centre = (centre[0] + (lat - centre[0]) / count,
                      centre[1] + (lon - centre[1]) / count)
            continue
        clusters.append([record])
        centre = (lat, lon)

    places: list[Place] = []
    for group in clusters:
        if len(group) < min_cluster:
            continue  # a frame or two in transit - not a place worth a folder
        coords = [_coordinates(r) for r in group]
        place = Place(
            index=len(places) + 1,
            latitude=sum(c[0] for c in coords) / len(coords),
            longitude=sum(c[1] for c in coords) / len(coords),
            records=group,
        )
        for record in group:
            record.place_id = place.index
        places.append(place)
    return places


def place_labels(places: list[Place]) -> dict[int, str]:
    """place_id -> folder name."""
    return {place.index: place.label for place in places}


def summarize(places: list[Place], total: int) -> str:
    """A single line for the status bar."""
    if not places:
        return "위치 정보 없음"
    grouped = sum(len(p.records) for p in places)
    return (f"장소 {len(places)}곳 · 위치 있는 컷 {grouped}/{total}장")
