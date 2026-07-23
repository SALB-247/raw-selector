"""LibRaw이 못 여는 니콘 NEF에서 메타데이터를 건지는 경로.

배경: 니콘 Z9의 고효율(HE/HE*) 압축은 intoPIX TicoRAW이고, raw 스트림 앞에
`CONTACT_INTOPIX_` 벤더 문자열이 박혀 있는 독점 규격입니다. LibRaw·dcraw·
darktable·RawTherapee 어디도 못 풉니다. **화소는 포기**하되, 메타데이터는
평범한 TIFF라 읽을 수 있습니다.

그래서 두 가지를 지킵니다.
  1. 화이트밸런스 — 없으면 보정 창의 색온도 조절이 통째로 죽습니다.
  2. 왜 못 여는지 — "실패"라고만 하면 파일이 깨진 줄 압니다.

파싱 오프셋은 틀리기 쉽습니다(실제로 처음 구현이 두 군데 틀렸습니다):
니콘 MakerNote 안의 오프셋은 파일이 아니라 **그 블록의 TIFF 헤더** 기준이고,
'0100' 형식 태그는 앞 4바이트가 버전 문자열입니다. 그래서 LibRaw이 여는
파일로 정답 대조를 겁니다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from arw_selector.core import nef_meta

# 니콘 Z9 실파일이 있는 폴더를 환경변수로 받습니다. 없으면 아래 테스트는
# 통째로 건너뜁니다 (샘플 NEF는 저장소에 넣지 않습니다).
SAMPLES = Path(os.environ.get("ARW_NIKON_SAMPLES", "_no_nikon_samples_"))
LOSSLESS = SAMPLES / "Nikon-Z9-raw-00010.nef"   # LibRaw이 여는 파일
HIGH_EFFICIENCY = SAMPLES / "Nikon-Z9-raw-00001.nef"  # LibRaw이 못 여는 파일

needs_samples = pytest.mark.skipif(
    not LOSSLESS.is_file() or not HIGH_EFFICIENCY.is_file(),
    reason="니콘 샘플 파일이 없습니다",
)


@needs_samples
def test_white_balance_matches_libraw():
    """정답 대조. 여기가 맞아야 LibRaw이 못 여는 파일의 값도 믿습니다."""
    rawpy = pytest.importorskip("rawpy")

    with rawpy.imread(str(LOSSLESS)) as raw:
        truth = tuple(float(x) for x in raw.camera_whitebalance)

    levels = nef_meta.read_white_balance_levels(LOSSLESS)
    assert levels is not None
    assert levels[0] == pytest.approx(truth[0], abs=1e-3), "R 불일치"
    assert levels[1] == pytest.approx(truth[1], abs=1e-3), "G 불일치"
    assert levels[2] == pytest.approx(truth[2], abs=1e-3), "B 불일치"


@needs_samples
def test_white_balance_recovered_when_libraw_cannot_open():
    """LibRaw이 파일을 아예 못 열어도 WB는 나와야 합니다."""
    from arw_selector.core.raw_io import read_white_balance

    balance = read_white_balance(HIGH_EFFICIENCY)
    assert balance is not None, "고효율 파일에서 WB를 못 건졌습니다"
    assert balance.camera[1] == pytest.approx(1.0, abs=1e-6)
    assert 2000 <= balance.as_shot_kelvin <= 12000, balance.as_shot_kelvin


@needs_samples
def test_compression_is_identified():
    """압축 방식을 정확히 읽어야 사유 안내가 성립합니다.

    '0100' 버전 접두사를 빼먹으면 문자 '0'(48)이 읽힙니다 — 처음에 그렇게
    만들었다가 두 파일 모두 48이 나왔습니다.
    """
    assert nef_meta.read_compression(LOSSLESS) == 3, "무손실이어야 합니다"
    assert nef_meta.read_compression(HIGH_EFFICIENCY) == 14, "HE*여야 합니다"


@needs_samples
def test_reason_only_for_unsupported():
    """열리는 파일에 경고를 달면 안 됩니다."""
    assert nef_meta.unsupported_reason(LOSSLESS) is None

    reason = nef_meta.unsupported_reason(HIGH_EFFICIENCY)
    assert reason, "사유가 나와야 합니다"
    assert "고효율" in reason
    # LibRaw 원문(`Unsupported file format or not RAW file`)은 파일이 깨진
    # 것처럼 읽혀서 그대로 보여 주면 안 됩니다
    assert "Unsupported file format" not in reason


def test_non_nef_is_left_alone(tmp_path):
    """다른 제조사 파일에 손대면 안 됩니다."""
    other = tmp_path / "사진.ARW"
    other.write_bytes(b"II\x2a\x00\x08\x00\x00\x00")
    assert nef_meta.unsupported_reason(other) is None


def test_garbage_file_does_not_raise(tmp_path):
    """깨진 파일을 물려도 예외가 새면 안 됩니다 — 표시 경로에서 도는 코드입니다."""
    broken = tmp_path / "깨진.nef"
    broken.write_bytes(b"\x00" * 64)
    assert nef_meta.read_white_balance_levels(broken) is None
    assert nef_meta.read_compression(broken) is None
    assert nef_meta.unsupported_reason(broken) is None


def test_missing_file_does_not_raise(tmp_path):
    missing = tmp_path / "없는파일.nef"
    assert nef_meta.read_white_balance_levels(missing) is None
    assert nef_meta.read_compression(missing) is None


@needs_samples
def test_other_makers_still_use_libraw():
    """폴백을 넣었다고 정상 경로가 달라지면 안 됩니다."""
    from arw_selector.core.raw_io import read_white_balance

    for name in ("Sony-a1-raw-00002.arw", "Panasonic-Lumix-S1R-raw-00007.rw2"):
        path = SAMPLES / name
        if not path.is_file():
            continue
        assert read_white_balance(path) is not None, name
