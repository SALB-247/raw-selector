"""The entry point that ties analysis -> grouping -> grading together.

The CLI and the GUI have to take the same path. If the two call things in a
different order, the same folder produces different results, and that is
the kind of bug that is extremely hard to debug.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import grouping, scoring
from .config import Config
from .pipeline import CancelCheck, ProgressCallback, analyze_folder, analyze_paths
from .types import Grade, ImageRecord

log = logging.getLogger(__name__)


@dataclass
class SelectionSession:
    """The state of the culling work on one folder."""

    folder: Path
    config: Config = field(default_factory=Config)
    records: list[ImageRecord] = field(default_factory=list)
    places: list = field(default_factory=list)
    """The list of places grouped by GPS (core/places.Place). Empty if
    there is no location."""

    def run(
        self,
        use_cache: bool = True,
        progress_cb: ProgressCallback | None = None,
        should_cancel: CancelCheck | None = None,
        paths: list[Path] | None = None,
        before_subjects: Callable[[list[ImageRecord]], object] | None = None,
        subject_cb: ProgressCallback | None = None,
    ) -> list[ImageRecord]:
        """Analyses and assigns grades as well.

        Given paths, only those files are looked at. Used when you want to
        check a few frames without going round the whole folder.

        before_subjects, if given, runs on the analysed records once they
        are grouped and before the batch's subject pass: the place for
        the GUI to put the user's saved main-subject picks back, so the
        pass leaves those frames alone instead of moving them first and
        having the picks undo it - a re-score on every run. subject_cb
        gets the pass's re-scores as (done, total); should_cancel stops
        them as it stops the analysis.
        """
        from .cache import default_cache_path
        from .subject import assign_subjects

        if paths:
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
        # The batch's subject (subject.py) needs the scenes for its
        # neighbour rule; regrade() groups again, cheaply.
        grouping.assign_groups(self.records, self.config.group)
        if before_subjects is not None:
            before_subjects(self.records)
        assign_subjects(self.records, self.config,
                        cache_path=default_cache_path(self.folder) if use_cache else None,
                        progress_cb=subject_cb, should_cancel=should_cancel)
        self.regrade()
        return self.records

    def regrade(self) -> list[ImageRecord]:
        """Recomputes the grouping and the grades only.

        There is no reason to re-analyse 4000 frames when a threshold
        changes. This path has to finish instantly for the GUI to let you
        adjust things by dragging a slider.
        """
        grouping.assign_groups(self.records, self.config.group)
        scoring.grade_records(self.records, self.config.score)
        # Places have nothing to do with grades, but they are refreshed
        # here as well. With not one frame carrying GPS it finishes
        # instantly, so there is no cost.
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
    """Applies the same processing to an arbitrary list of files (for tests
    and partial reprocessing)."""
    config = config or Config()
    records = analyze_paths(paths, config=config, cache_path=cache_path)
    grouping.assign_groups(records, config.group)
    from .subject import assign_subjects

    assign_subjects(records, config, cache_path=cache_path)
    scoring.grade_records(records, config.score)
    return records
