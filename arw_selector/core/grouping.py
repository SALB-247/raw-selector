"""유사 컷 그룹핑.

4000장은 실제로는 "비슷한 컷 3~10장 × 수백 그룹"입니다. 전체를 한 줄로 세워
상위 N장을 뽑으면 잘 나온 장면 하나가 셀렉트를 독식하고 다른 장면은 통째로
빠집니다. 그룹을 만들고 그룹마다 베스트를 뽑아야 셀렉터로서 쓸모가 있습니다.

경계 판정은 촬영 시각을 주 신호로 씁니다. 실측에서 시간은 연사와 장면 전환을
깨끗하게 갈랐지만(연사 내 0.16초 vs 전환 수십 초), 시각적 유사도는 그러지
못했다 — 망원으로 움직이는 피사체는 0.16초 사이에도 화면이 크게 바뀌어서
같은 연사의 해시 거리 분포와 장면 전환의 분포가 겹칩니다. 자세한 수치는
GroupConfig.scene_change_distance 주석에 있습니다.

그래서 지각적 해시는 "명백한 전환"만 잡는 보조 신호로 두고, 시각 정보가
유일한 근거인 경우(EXIF 시각 없음)에만 임계값을 조입니다.
"""

from __future__ import annotations

import cv2
import numpy as np

from .config import GroupConfig
from .types import ImageRecord

DHASH_SIZE = 8
"""8x8 비교 → 64비트 해시."""


def dhash(image_bgr: np.ndarray, size: int = DHASH_SIZE) -> int:
    """difference hash. 인접 픽셀의 밝기 대소 관계만 남깁니다.

    노출과 화이트밸런스가 흔들려도 같은 장면이면 값이 거의 유지되므로
    연사 묶기에 적합합니다.
    """
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY) if image_bgr.ndim == 3 else image_bgr
    resized = cv2.resize(gray, (size + 1, size), interpolation=cv2.INTER_AREA)
    bits = resized[:, 1:] > resized[:, :-1]

    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return value


def hamming_distance(a: int, b: int) -> int:
    """두 해시가 몇 비트나 다른지. 0이면 동일한 화면."""
    return bin(a ^ b).count("1")


def _sort_key(record: ImageRecord):
    """촬영 시각 우선, 없으면 파일명. 연사는 서브초까지 봐야 순서가 맞습니다."""
    capture = record.metadata.capture_time if record.metadata else None
    return (0, capture, record.path.name) if capture else (1, None, record.path.name)


def _seconds_between(a: ImageRecord, b: ImageRecord) -> float | None:
    if not (a.metadata and b.metadata):
        return None
    if not (a.metadata.capture_time and b.metadata.capture_time):
        return None
    return abs((b.metadata.capture_time - a.metadata.capture_time).total_seconds())


def assign_groups(
    records: list[ImageRecord], config: GroupConfig | None = None
) -> list[ImageRecord]:
    """record.group_id를 채워서 그대로 돌려준다 (제자리 수정).

    입력 리스트의 순서는 바꾸지 않는다 — 호출자가 기대하는 순서가 따로 있습니다.
    """
    config = config or GroupConfig()
    if not records:
        return records

    ordered = sorted(records, key=_sort_key)

    group_id = 0
    anchor = ordered[0]
    group_size = 0

    for index, record in enumerate(ordered):
        if index == 0:
            record.group_id = group_id
            group_size = 1
            continue

        previous = ordered[index - 1]
        gap = _seconds_between(previous, record)

        # 화면 비교는 앵커(그룹 첫 장)와 합니다. 직전 장과만 비교하면 조금씩
        # 달라지는 팬 촬영이 하나의 거대한 그룹으로 이어져 버립니다.
        distance = None
        if record.dhash is not None and anchor.dhash is not None:
            distance = hamming_distance(record.dhash, anchor.dhash)

        if gap is not None:
            # 시간을 신뢰합니다. 화면 변화는 명백한 전환일 때만 개입시킵니다.
            visual_split = (
                distance is not None and distance > config.scene_change_distance
            )
            starts_new_group = gap > config.time_gap_seconds or visual_split
        else:
            # EXIF 시각이 없으면 화면 변화가 유일한 근거다 — 임계를 조입니다.
            starts_new_group = (
                distance is not None and distance > config.no_time_hash_distance
            )

        starts_new_group = starts_new_group or group_size >= config.max_group_size

        if starts_new_group:
            group_id += 1
            anchor = record
            group_size = 0

        record.group_id = group_id
        group_size += 1

    return records


def group_counts(records: list[ImageRecord]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for record in records:
        if record.group_id is not None:
            counts[record.group_id] = counts.get(record.group_id, 0) + 1
    return counts
