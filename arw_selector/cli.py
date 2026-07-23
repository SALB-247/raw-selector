"""헤드리스 CLI. 4000장 배치 검증과 자동화용.

GUI와 완전히 같은 코어를 쓴다 (core.session). 여기서 나온 결과와 GUI에서
나온 결과가 다르면 그것은 버급니다.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from pathlib import Path

from .core import export as export_module
from .core.config import Config
from .core.reason_text import render_all
from .core.session import SelectionSession
from .core.types import Grade, ImageRecord

log = logging.getLogger(__name__)


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}초"
    return f"{int(seconds // 60)}분 {seconds % 60:.0f}초"


class ProgressPrinter:
    """터미널 한 줄을 덮어쓰며 진행률을 보여 줍니다."""

    def __init__(self, quiet: bool = False):
        self.quiet = quiet
        self._last_done = -1

    def __call__(self, progress) -> None:
        if self.quiet or progress.total == 0:
            return
        # 매 장마다 다시 그리면 터미널이 병목이 됩니다
        if progress.done - self._last_done < 25 and progress.done != progress.total:
            return
        self._last_done = progress.done

        eta = progress.eta_seconds
        eta_text = f" ETA {_format_duration(eta)}" if eta else ""
        bar_width = 30
        filled = int(bar_width * progress.ratio)
        bar = "█" * filled + "·" * (bar_width - filled)
        sys.stdout.write(
            f"\r  [{bar}] {progress.done}/{progress.total}"
            f" ({progress.ratio * 100:.0f}%){eta_text}   "
        )
        sys.stdout.flush()
        if progress.done == progress.total:
            sys.stdout.write("\n")


def write_report(records: list[ImageRecord], path: Path) -> None:
    """장별 상세를 CSV 또는 JSON으로 남깁니다. 임계값 튜닝의 근거가 됩니다."""
    path = Path(path)

    if path.suffix.lower() == ".json":
        payload = [
            {
                "path": str(r.path),
                "grade": r.final_grade.value,
                "score": round(r.score, 2),
                "group_id": r.group_id,
                "group_rank": r.group_rank,
                "sharpness": round(r.focus.sharpness, 2) if r.focus else None,
                "frame_sharpness": round(r.focus.frame_sharpness, 2) if r.focus else None,
                "focus_source": r.focus.source.value if r.focus else None,
                "face_count": r.focus.face_count if r.focus else 0,
                "capture_time": (
                    r.metadata.capture_time.isoformat()
                    if r.metadata and r.metadata.capture_time
                    else None
                ),
                "lens": r.metadata.lens_model if r.metadata else None,
                "iso": r.metadata.iso if r.metadata else None,
                "shutter": r.metadata.shutter_display if r.metadata else None,
                "reasons": render_all(r.reasons),
                "error": r.error,
            }
            for r in records
        ]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return

    # utf-8-sig: Excel이 BOM 없이는 한글을 깨뜨립니다
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "파일", "등급", "점수", "그룹", "그룹내순위", "ROI선명도",
            "전체선명도", "판정기준", "얼굴수", "촬영시각", "렌즈", "ISO",
            "셔터", "사유", "오류",
        ])
        for r in records:
            writer.writerow([
                r.path.name,
                r.final_grade.value,
                f"{r.score:.2f}",
                r.group_id,
                r.group_rank,
                f"{r.focus.sharpness:.2f}" if r.focus else "",
                f"{r.focus.frame_sharpness:.2f}" if r.focus else "",
                r.focus.source.value if r.focus else "",
                r.focus.face_count if r.focus else 0,
                r.metadata.capture_time.isoformat() if r.metadata and r.metadata.capture_time else "",
                r.metadata.lens_model if r.metadata else "",
                r.metadata.iso if r.metadata else "",
                r.metadata.shutter_display if r.metadata else "",
                "; ".join(render_all(r.reasons)),
                r.error or "",
            ])


def print_summary(session: SelectionSession, elapsed: float) -> None:
    summary = session.summary
    total = len(session.records)

    print(f"\n{'=' * 52}")
    print(f"  {session.folder}")
    print(f"{'=' * 52}")
    print(f"  총 {total}장, {session.group_count}개 장면, {_format_duration(elapsed)}")
    if total:
        print(f"  장당 {elapsed / total * 1000:.0f}ms")
    print()
    for grade, label in [
        (Grade.KEEP, "keep  "),
        (Grade.REVIEW, "review"),
        (Grade.REJECT, "reject"),
    ]:
        count = summary[grade.value]
        ratio = count / total * 100 if total else 0
        bar = "█" * int(ratio / 2.5)
        print(f"  {label}  {count:>5}장  {ratio:>5.1f}%  {bar}")

    failed = session.failed
    if failed:
        print(f"\n  분석 실패 {len(failed)}장:")
        for record in failed[:5]:
            print(f"    - {record.path.name}: {record.error}")
        if len(failed) > 5:
            print(f"    ... 외 {len(failed) - 5}장")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arw-select",
        description="RAW 초점 기준 셀렉트 (ARW/CR3/NEF/RAF 등)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""예시:
  arw-select D:/shoot                      분석만 하고 결과 요약
  arw-select D:/shoot --report out.csv     장별 상세를 CSV로
  arw-select D:/shoot --export             _keep/_review/_reject로 복사
  arw-select D:/shoot --export --move      복사 대신 이동
  arw-select D:/shoot --undo               마지막 내보내기 되돌리기
""",
    )
    parser.add_argument("folder", type=Path, nargs="?", help="RAW 파일이 있는 폴더")
    parser.add_argument("--config", type=Path, help="설정 YAML 경로")
    parser.add_argument("--report", type=Path, help="장별 상세 출력 (.csv 또는 .json)")
    parser.add_argument(
        "--export", nargs="?", const=True, default=False, metavar="DIR",
        help="등급별 폴더로 내보내기 (경로 생략 시 원본 폴더 안에 생성)",
    )
    parser.add_argument(
        "--grades", default=None, metavar="LIST",
        help="내보낼 등급만 지정 (쉼표 구분: keep,review,reject). 기본은 전체",
    )
    parser.add_argument("--move", action="store_true", help="복사 대신 이동 (되돌리기 주의)")
    parser.add_argument("--dry-run", action="store_true", help="무엇이 어디로 갈지만 확인")
    parser.add_argument("--undo", action="store_true", help="가장 최근 내보내기를 되돌린다")
    parser.add_argument("--no-cache", action="store_true", help="캐시 무시하고 전부 재분석")
    parser.add_argument("--workers", type=int, help="병렬 워커 수 (기본: CPU-1)")
    parser.add_argument("--keep-per-group", type=int, help="장면당 keep 장수")
    parser.add_argument(
        "--target-keep", type=float, metavar="PCT",
        help="목표 keep 비율 %% (예: 10). 배치 점수 분포에서 임계값을 역산합니다",
    )
    parser.add_argument(
        "--keep-above", type=float, metavar="SCORE",
        help="keep 절대 점수. 지정하면 목표 비율 대신 이 값을 씁니다",
    )
    parser.add_argument("--recursive", action="store_true", default=None, help="하위 폴더 포함")
    parser.add_argument("--no-recursive", dest="recursive", action="store_false")
    parser.add_argument("-q", "--quiet", action="store_true", help="진행률 숨김")
    parser.add_argument("-v", "--verbose", action="store_true", help="상세 로그")
    parser.add_argument("--dump-config", action="store_true", help="현재 설정을 YAML로 출력")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    config = Config.load(args.config)
    if args.workers:
        config.workers = args.workers
    if args.keep_per_group:
        config.score.keep_per_group = args.keep_per_group
    if args.target_keep is not None:
        config.score.target_keep_ratio = args.target_keep / 100.0
    if args.keep_above is not None:
        # 절대 점수를 명시했으면 목표 비율은 꺼야 합니다 — 둘 다 켜면 비율이 이깁니다
        config.score.keep_above = args.keep_above
        config.score.target_keep_ratio = None
    if args.recursive is not None:
        config.recursive = args.recursive

    if args.dump_config:
        print(config.to_yaml())
        return 0

    if args.folder is None:
        parser.error("폴더를 지정해야 한다 (--dump-config 제외)")

    folder = args.folder.expanduser().resolve()
    if not folder.is_dir():
        print(f"폴더가 아닙니다: {folder}", file=sys.stderr)
        return 2

    if args.undo:
        return _run_undo(folder)

    session = SelectionSession(folder=folder, config=config)

    if not args.quiet:
        print(f"분석 중: {folder}")

    started = time.perf_counter()
    session.run(use_cache=not args.no_cache, progress_cb=ProgressPrinter(args.quiet))
    elapsed = time.perf_counter() - started

    if not session.records:
        print("RAW 파일을 찾지 못했습니다.", file=sys.stderr)
        return 1

    print_summary(session, elapsed)

    if args.report:
        write_report(session.records, args.report)
        print(f"\n  리포트: {args.report}")

    if args.export:
        destination = folder if args.export is True else Path(args.export)
        return _run_export(session, destination, args)

    return 0


def _select_for_export(session: SelectionSession, grades_arg: str | None) -> list[ImageRecord]:
    """--grades로 지정한 등급만 남깁니다. 없으면 전체."""
    if not grades_arg:
        return session.records

    wanted = set()
    for token in grades_arg.split(","):
        token = token.strip().lower()
        if not token:
            continue
        try:
            wanted.add(Grade(token))
        except ValueError:
            raise SystemExit(
                f"알 수 없는 등급: {token} (가능한 값: keep, review, reject)"
            )
    return [r for r in session.records if r.final_grade in wanted]


def _run_export(session: SelectionSession, destination: Path, args) -> int:
    mode = "이동" if args.move else "복사"
    records = _select_for_export(session, args.grades)

    if not records:
        print(f"\n  내보낼 대상이 없다 (--grades {args.grades})", file=sys.stderr)
        return 1

    scope = f" [{args.grades}만 {len(records)}장]" if args.grades else ""
    print(f"\n  {mode} 중{scope}: {destination}")

    result = export_module.export_records(
        records,
        destination,
        move=args.move,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        plan = export_module.build_plan(records, destination)
        print(f"  dry-run: {len(plan.operations)}개 파일이 이동 대상")
        for op in plan.operations[:5]:
            print(f"    {op.source.name} -> {op.destination.parent.name}/")
        if len(plan.operations) > 5:
            print(f"    ... 외 {len(plan.operations) - 5}개")
        return 0

    print(f"  완료: {result.moved}개")
    if result.failed:
        print(f"  실패: {len(result.failed)}개")
        for path, reason in result.failed[:5]:
            print(f"    - {path.name}: {reason}")
    if result.log_path:
        print(f"  되돌리기: arw-select {destination} --undo")
    return 0 if not result.failed else 1


def _run_undo(folder: Path) -> int:
    logs = export_module.find_logs(folder)
    if not logs:
        print(f"되돌릴 내보내기 기록이 없습니다: {folder}", file=sys.stderr)
        return 1

    print(f"되돌리는 중: {logs[0].name}")
    result = export_module.undo_export(logs[0])
    print(f"  완료: {result.moved}개")
    if result.failed:
        print(f"  실패: {len(result.failed)}개")
        for path, reason in result.failed[:5]:
            print(f"    - {path.name}: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
