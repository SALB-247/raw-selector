"""분석 → 그룹핑 → 등급 판정을 하나로 묶은 진입점.

CLI와 GUI가 같은 경로를 타야 합니다. 둘이 다른 순서로 호출하면 같은 폴더에
대해 다른 결과가 나오고, 그것은 디버깅이 매우 어려운 종류의 버급니다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from . import grouping, scoring
from .config import Config
from .pipeline import CancelCheck, ProgressCallback, analyze_folder, analyze_paths
from .types import Grade, ImageRecord

log = logging.getLogger(__name__)


@dataclass
class SelectionSession:
    """한 폴더에 대한 셀렉트 작업 상태."""

    folder: Path
    config: Config = field(default_factory=Config)
    records: list[ImageRecord] = field(default_factory=list)
    places: list = field(default_factory=list)
    """GPS로 묶은 장소 목록 (core/places.Place). 위치가 없으면 빕니다."""

    def run(
        self,
        use_cache: bool = True,
        progress_cb: ProgressCallback | None = None,
        should_cancel: CancelCheck | None = None,
        paths: list[Path] | None = None,
    ) -> list[ImageRecord]:
        """분석하고 등급까지 매깁니다.

        paths를 주면 그 파일들만 봅니다. 폴더 전체를 돌 필요 없이 몇 장만
        확인하고 싶을 때 씁니다.
        """
        if paths:
            from .cache import default_cache_path

            self.records = analyze_paths(
                paths,
                config=self.config,
                cache_path=default_cache_path(self.folder),
                use_cache=use_cache,
                progress_cb=progress_cb,
                should_cancel=should_cancel,
            )
        else:
            self.records = analyze_folder(
                self.folder,
                config=self.config,
                use_cache=use_cache,
                progress_cb=progress_cb,
                should_cancel=should_cancel,
            )
        self.regrade()
        return self.records

    def regrade(self) -> list[ImageRecord]:
        """그룹핑과 등급만 다시 계산합니다.

        임계값을 바꿨을 때 4000장을 재분석할 이유가 없습니다. 이 경로는 즉시
        끝나야 GUI에서 슬라이더를 움직이며 조정할 수 있습니다.
        """
        grouping.assign_groups(self.records, self.config.group)
        scoring.grade_records(self.records, self.config.score)
        # 장소는 등급과 무관하지만 여기서 함께 갱신합니다. GPS가 있는 컷이
        # 하나도 없으면 즉시 끝나므로 비용이 없습니다.
        from . import places as places_module

        self.places = places_module.assign_places(self.records)
        return self.records

    @property
    def summary(self) -> dict[str, int]:
        return scoring.summarize(self.records)

    @property
    def group_count(self) -> int:
        return len({r.group_id for r in self.records if r.group_id is not None})

    @property
    def failed(self) -> list[ImageRecord]:
        return [r for r in self.records if not r.ok]

    def by_grade(self, grade: Grade) -> list[ImageRecord]:
        return [r for r in self.records if r.final_grade == grade]


def analyze_and_grade(
    paths: list[Path], config: Config | None = None, cache_path: Path | None = None
) -> list[ImageRecord]:
    """임의의 파일 목록에 대해 같은 처리를 적용한다 (테스트/부분 재처리용)."""
    config = config or Config()
    records = analyze_paths(paths, config=config, cache_path=cache_path)
    grouping.assign_groups(records, config.group)
    scoring.grade_records(records, config.score)
    return records
