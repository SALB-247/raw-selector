"""GPS가 있는 컷을 같은 장소끼리 묶습니다.

왜 시간(장면) 그룹과 따로 두는가
--------------------------------
`grouping.py`의 그룹은 **연사 묶음**입니다 — 3초 안의 비슷한 컷 3~10장이
한 그룹이고, 그 안에서 베스트를 뽑는 용도입니다. 장소는 성격이 완전히
다릅니다. 하루에 세 군데를 돌면 장소는 3개인데 장면은 수백 개입니다.

그래서 별도의 축으로 둡니다. 내보낼 때 장소별 폴더로 나누거나, 격자에서
"이 장소만" 보는 데 씁니다.

정확도에 대해
-------------
동네 이름을 붙이지 않습니다. 그러려면 온라인 지오코딩이 필요한데, 사진의
좌표를 외부 서버로 보내는 일이라 이 도구가 할 일이 아닙니다. 대신 좌표
자체와 묶음 번호만 만들고, 이름은 사용자가 폴더를 보고 붙이면 됩니다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .types import ImageRecord

DEFAULT_RADIUS_M = 250.0
"""같은 장소로 볼 반경(m).

공연장 하나, 공원 하나 정도가 이 안에 들어옵니다. 더 좁히면 폰 GPS의
오차(도심에서 흔히 20~50m, 실내는 더 큼)만으로도 한 공연장이 둘로
쪼개집니다. 더 넓히면 같은 동네의 다른 장소가 합쳐집니다.
"""

MIN_CLUSTER = 2
"""이 장수 미만이면 독립 장소로 만들지 않고 '기타'로 둡니다.

이동 중에 한 장 찍힌 좌표까지 폴더를 만들면 폴더가 수십 개가 됩니다.
"""

_EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 좌표 사이 거리(m).

    평면 근사를 쓰면 고위도에서 경도 1도의 실제 거리가 크게 줄어드는 것을
    반영하지 못합니다. 위도 60도에서 오차가 2배입니다.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    return 2.0 * _EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


@dataclass
class Place:
    """한 장소. 좌표는 속한 컷들의 평균입니다."""

    index: int
    latitude: float
    longitude: float
    records: list[ImageRecord] = field(default_factory=list)

    @property
    def label(self) -> str:
        """폴더 이름으로 쓸 문자열. 좌표를 소수 4자리(≈11m)까지."""
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
    """GPS가 있는 컷에 place_id를 매기고 장소 목록을 돌려줍니다.

    좌표가 없는 컷은 place_id가 None으로 남습니다 — GPS 없이 찍은 컷이
    임의의 장소에 섞이면 안 됩니다.

    묶는 방식은 **촬영 시각 순서대로 훑으며 이어 붙이기**입니다. 사진은
    이동 경로를 따라 찍히므로, 시간순으로 보면 같은 장소가 연속으로
    나타납니다. 전역 클러스터링(k-means 등)은 장소 개수를 미리 알아야 하고,
    이동 중 찍은 한 장이 두 장소를 다리처럼 이어 붙이는 문제가 있습니다.
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
            # 중심을 누적 평균으로 갱신합니다. 첫 장에 고정하면 행사장을
            # 가로질러 걸을 때 뒤쪽 컷이 반경을 벗어나 갈라집니다.
            count = len(clusters[-1])
            centre = (centre[0] + (lat - centre[0]) / count,
                      centre[1] + (lon - centre[1]) / count)
            continue
        clusters.append([record])
        centre = (lat, lon)

    places: list[Place] = []
    for group in clusters:
        if len(group) < min_cluster:
            continue  # 이동 중 한두 장 — 폴더를 만들 만한 장소가 아닙니다
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
    """place_id → 폴더 이름."""
    return {place.index: place.label for place in places}


def summarize(places: list[Place], total: int) -> str:
    """상태 표시줄용 한 줄."""
    if not places:
        return "위치 정보 없음"
    grouped = sum(len(p.records) for p in places)
    return (f"장소 {len(places)}곳 · 위치 있는 컷 {grouped}/{total}장")
