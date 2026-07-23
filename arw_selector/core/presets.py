"""프리셋 저장소.

판정 기준과 보정 설정 양쪽이 같은 구조를 씁니다. 사용자 홈의 설정 폴더에
YAML로 저장하므로 다른 촬영, 다른 세션에서도 그대로 불러 쓸 수 있고
파일을 직접 열어 편집하거나 남에게 넘길 수도 있습니다.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

from .appinfo import (  # noqa: F401 (재수출)
    APP_DIR_NAME,
    LEGACY_APP_DIR_NAMES,
    _config_root,
    data_dir,
)

SELECT_PRESET_DIR = "select_presets"
DEVELOP_PRESET_DIR = "develop_presets"

_SAFE_NAME = re.compile(r"[^\w가-힣 _-]+")


def user_config_dir() -> Path:
    """플랫폼별 설정 폴더.

    프리셋·렌즈 프로필은 실행 파일 옆 data/ 에 둡니다. 앱 폴더를 통째로
    옮기면 함께 따라가야 자연스럽기 때문입니다. 쓰기가 막힌 위치에
    설치했을 때만 사용자 폴더로 물러납니다(appinfo.data_dir 참고).
    """
    return data_dir()


def migrate_legacy_config() -> Path | None:
    """예전 위치에 저장된 프리셋을 지금 쓰는 데이터 폴더로 복사합니다.

    두 번의 이사를 모두 흡수합니다:
      1) %APPDATA%/arw_selector   (예전 제품명)
      2) %APPDATA%/raw_selector   (제품명은 같지만 AppData에 두던 시절)
    지금은 실행 파일 옆 data/ 가 기본이라, 위 둘 중 먼저 발견되는 것을
    가져옵니다. 이걸 빠뜨리면 사용자가 만든 프리셋이 통째로 사라진 것처럼
    보입니다.

    옮기지 않고 **복사**합니다. 이 툴의 기본 원칙(되돌릴 수 없는 일은 하지
    않는다)에 맞추고, 예전 버전을 다시 실행해도 그대로 동작하게 두기
    위해서입니다. 대상에 이미 프리셋이 있으면 건드리지 않습니다.

    돌려주는 값은 실제로 복사해 온 예전 폴더 경로 (없었으면 None).
    """
    import shutil

    target = user_config_dir()
    # 이미 프리셋이 하나라도 있으면 사용자의 현재 작업물이므로 덮지 않습니다
    for subdir in (SELECT_PRESET_DIR, DEVELOP_PRESET_DIR):
        existing = target / subdir
        if existing.is_dir() and any(existing.glob("*.yaml")):
            return None

    root = _config_root()
    candidates = [root / APP_DIR_NAME, *(root / name for name in LEGACY_APP_DIR_NAMES)]
    for legacy in candidates:
        if legacy.resolve() == target.resolve() or not legacy.is_dir():
            continue
        if not any((legacy / sub).is_dir() for sub in (SELECT_PRESET_DIR, DEVELOP_PRESET_DIR)):
            continue
        try:
            target.mkdir(parents=True, exist_ok=True)
            for subdir in (SELECT_PRESET_DIR, DEVELOP_PRESET_DIR, "lensfun"):
                source = legacy / subdir
                if source.is_dir():
                    shutil.copytree(source, target / subdir, dirs_exist_ok=True)
        except OSError as exc:
            log.warning("예전 프리셋을 옮기지 못했습니다 (%s): %s", legacy, exc)
            return None
        log.info("예전 프리셋을 옮겨 왔습니다: %s -> %s", legacy, target)
        return legacy
    return None


def safe_filename(name: str) -> str:
    """프리셋 이름을 파일명으로 쓸 수 있게 정리합니다.

    사용자가 이름에 슬래시나 '..'을 넣어도 지정한 폴더 밖으로 나가지
    않아야 합니다.
    """
    cleaned = _SAFE_NAME.sub("", name).strip()
    cleaned = cleaned.replace("..", "").strip(". ")
    return cleaned[:80] or "이름없음"


@dataclass(frozen=True)
class PresetInfo:
    name: str
    path: Path
    modified: datetime

    @property
    def display(self) -> str:
        return self.name


class PresetStore:
    """한 종류의 프리셋을 다루는 저장소."""

    def __init__(self, subdirectory: str, root: Path | None = None):
        self.directory = (root or user_config_dir()) / subdirectory

    def ensure_dir(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[PresetInfo]:
        """이름 순으로 반환합니다.

        stat이 잠깐 실패해도(백신이 방금 저장한 파일을 스캔하며 순간적으로
        잠그는 경우) 프리셋을 목록에서 빼지 않습니다. 그렇지 않으면 방금
        저장한 프리셋이 화면에서 사라진 것처럼 보입니다.
        """
        if not self.directory.exists():
            return []
        items = []
        for path in self.directory.glob("*.yaml"):
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                modified = datetime.min
            items.append(PresetInfo(name=path.stem, path=path, modified=modified))
        return sorted(items, key=lambda p: p.name.lower())

    def path_for(self, name: str) -> Path:
        return self.directory / f"{safe_filename(name)}.yaml"

    def exists(self, name: str) -> bool:
        return self.path_for(name).exists()

    def save(self, name: str, data: dict[str, Any]) -> Path:
        self.ensure_dir()
        path = self.path_for(name)
        payload = {
            "name": name,
            "saved": datetime.now().isoformat(timespec="seconds"),
            "data": data,
        }
        path.write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return path

    def load(self, name_or_path: str | Path) -> dict[str, Any]:
        """프리셋 내용을 돌려줍니다.

        손상된 파일은 **OSError 아니면 ValueError**로 던집니다. 화면 쪽은
        전부 그 둘만 잡아 경고창을 띄우는데(gui/preset_bar.py 등), YAML
        파서가 내는 `yaml.YAMLError`는 ValueError가 아니라서 그 그물을
        빠져나갑니다. 그러면 Qt 슬롯 밖으로 새어나가 사용자에게는 경고창
        대신 앱이 사라지는 것으로 보입니다. 여기서 바꿔 둡니다.
        """
        path = (
            Path(name_or_path)
            if isinstance(name_or_path, Path) or str(name_or_path).endswith(".yaml")
            else self.path_for(str(name_or_path))
        )
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"프리셋을 읽지 못했습니다 ({path.name}): {exc}") from exc
        if not isinstance(payload, dict) or "data" not in payload:
            raise ValueError(f"프리셋 형식이 아닙니다: {path.name}")
        data = payload["data"]
        if not isinstance(data, dict):
            raise ValueError(f"프리셋 내용이 비어 있습니다: {path.name}")
        return data

    def delete(self, name: str) -> bool:
        path = self.path_for(name)
        try:
            path.unlink()
            return True
        except OSError:
            return False


def select_presets(root: Path | None = None) -> PresetStore:
    """판정 기준 프리셋."""
    return PresetStore(SELECT_PRESET_DIR, root)


def develop_presets(root: Path | None = None) -> PresetStore:
    """보정 프리셋."""
    return PresetStore(DEVELOP_PRESET_DIR, root)


def default_develop_profiles() -> dict[str, dict]:
    """기본으로 제공하는 카메라 프로파일 프리셋.

    디모자이크 베이스라인(표준)에 얹는 '룩'입니다. 색온도는 컷마다 달라야
    하므로 넣지 않아, 어떤 사진에도 그대로 적용할 수 있습니다.
    """
    from .develop import (
        BasicSettings,
        ColorGradeSettings,
        ColorGradeZone,
        DevelopSettings,
        HSLBand,
        HSLSettings,
    )

    def hsl(**bands: HSLBand) -> HSLSettings:
        return HSLSettings(bands=dict(bands))

    profiles = {
        # 표준 = 베이스라인 그대로 (룩 초기화용)
        "표준": DevelopSettings(),
        "인물": DevelopSettings(
            basic=BasicSettings(contrast=-8, clarity=-12, vibrance=10, saturation=-3),
            hsl=hsl(orange=HSLBand(luminance=8, saturation=-5),
                    red=HSLBand(saturation=-4)),
        ),
        "풍경": DevelopSettings(
            basic=BasicSettings(contrast=14, clarity=8, vibrance=18, saturation=4),
            hsl=hsl(blue=HSLBand(saturation=10, luminance=-6),
                    green=HSLBand(saturation=8)),
        ),
        "선명": DevelopSettings(
            basic=BasicSettings(contrast=20, clarity=12, vibrance=12, saturation=20),
        ),
        "필름": DevelopSettings(
            basic=BasicSettings(contrast=-14, blacks=18, whites=-8, saturation=-10),
            color_grade=ColorGradeSettings(
                shadows=ColorGradeZone(hue=200, saturation=12),
                highlights=ColorGradeZone(hue=45, saturation=10),
                blending=50,
            ),
        ),
        "중립": DevelopSettings(
            basic=BasicSettings(contrast=-18, saturation=-8, clarity=-4),
        ),
    }
    return {name: settings.to_dict() for name, settings in profiles.items()}


def install_default_profiles(root: Path | None = None) -> int:
    """기본 프로파일 프리셋을 한 번 설치합니다. 설치한 개수를 반환.

    이미 설치했으면(마커 존재) 건너뜁니다. 사용자가 지운 프리셋을 매번
    되살리지 않도록 마커로 한 번만 씁니다. 같은 이름을 사용자가 이미
    쓰고 있으면 덮어쓰지 않습니다.
    """
    store = develop_presets(root)
    store.ensure_dir()
    marker = store.directory / ".profiles_installed"
    if marker.exists():
        return 0
    installed = 0
    for name, data in default_develop_profiles().items():
        if not store.exists(name):
            store.save(name, data)
            installed += 1
    marker.write_text("1", encoding="utf-8")
    return installed


def default_select_presets() -> dict[str, dict]:
    """기본으로 제공하는 판정(셀렉트) 프리셋.

    코드 기본값(ScoreConfig·GroupConfig) 그대로입니다. 개인 촬영 맥락이 담긴
    프리셋을 배포본에 담지 않으려고, 배포에는 이 일반값만 코드에서 만들어
    넣습니다 — 보정 프리셋과 같은 방식입니다.
    """
    from dataclasses import asdict

    from .config import GroupConfig, ScoreConfig

    return {
        "기본": {
            "score": asdict(ScoreConfig()),
            "group": asdict(GroupConfig()),
        }
    }


def install_default_select_presets(root: Path | None = None) -> int:
    """기본 판정 프리셋을 한 번 설치합니다. 설치한 개수를 반환.

    install_default_profiles와 같은 규칙입니다(마커로 한 번만, 사용자 것은
    덮지 않음). 예전에는 판정 프리셋에 기본값이 없어, 개인 프리셋을 배포에
    담지 않으면 배포본에 판정 프리셋이 아예 없었습니다.
    """
    store = select_presets(root)
    store.ensure_dir()
    marker = store.directory / ".select_installed"
    if marker.exists():
        return 0
    installed = 0
    for name, data in default_select_presets().items():
        if not store.exists(name):
            store.save(name, data)
            installed += 1
    marker.write_text("1", encoding="utf-8")
    return installed
