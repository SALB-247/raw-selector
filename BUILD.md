# 빌드 및 배포

## Windows

### 준비

```bash
pip install -e ".[gui,lens,dev]"
pip install nuitka
```

Nuitka는 첫 빌드에서 C 컴파일러를 내려받습니다(`--assume-yes-for-downloads`가
자동 승인). Visual Studio Build Tools가 이미 설치되어 있으면 그것을 씁니다.

### 빌드

```bash
python build_windows.py --clean
```

결과물은 `build/launcher.dist/` 폴더이며, 실행 파일은 `RAW_selector.exe`입니다.
빌드가 끝나면 자동으로 세 가지를 더 합니다.

1. `배포_읽어보세요.txt` · `자체점검.bat` · `CHANGELOG.md`를 배포 폴더에 넣습니다
2. 빠진 데이터가 없는지 셉니다 (렌즈 XML, 보정·판정 프리셋, ONNX 모델 두 개)
3. `build/RAW_selector_<버전>_win64.zip` 으로 묶고 무결성을 확인합니다
   (0.14.0 실측 101MB, 원본 대비 37%)

실측(0.14.0, Nuitka 4.1.3 / MSVC 14.3): **파일 286개, 270.9MB, exe 28.7MB.**

배포할 때는 `launcher.dist` 폴더 전체(또는 zip)를 함께 전달해야 합니다.
파일 하나만 빠져도 받는 쪽에서 원인을 찾기 어려우므로 zip 쪽이 안전합니다.

플래그:

| 플래그 | 뜻 |
|---|---|
| `--clean` | 이전 빌드 폴더를 지우고 시작 |
| `--no-zip` | zip 묶기를 건너뜀 |
| `--zip-only` | 이미 빌드된 폴더를 zip으로만 묶음 (다시 빌드하지 않음) |
| `--no-version-info` | exe에 제품명·설명을 넣지 않음 (아래 백신 절 참고) |
| `--onefile` | 단일 exe |

`--onefile`은 권장하지 않습니다. 실행할 때마다 임시 폴더에 전체를 풀어 시작이
느리고, 병렬 분석이 자식 프로세스를 띄울 때 문제가 생길 수 있습니다.

### 진입점에 관한 주의

빌드 진입점은 `arw_selector/gui/app.py`가 아니라 **`launcher.py`** 입니다.

`app.py`를 직접 지정하면 그 모듈이 `__main__`이 되면서 패키지 맥락을 잃고
상대 임포트(`from ..core...`)가 실패합니다. 실제로 다음 오류가 발생합니다.

```
ImportError: attempted relative import with no known parent package
```

`launcher.py`는 절대 임포트만 사용하는 얇은 진입점이라 이 문제를 피합니다.

### 포함해야 하는 데이터

- `arw_selector/core/models/face_detection_yunet_2023mar.onnx` — 얼굴 검출 모델.
  빠지면 얼굴 인식이 동작하지 않고 타일 기반 판정으로만 떨어집니다.
- `arw_selector/core/models/face_mesh_192x192.onnx` — 468점 얼굴 윤곽 모델.
  빠지면 얼굴·눈 마스크가 타원 근사로 떨어지고 **눈 감김 감점이 아예 걸리지
  않습니다**(EAR을 못 재면 감점하지 않도록 되어 있기 때문입니다).
- `lensfunpy/db_files/` — 렌즈 프로필 데이터베이스. 빠지면 자동 렌즈 보정이
  동작하지 않습니다(수동 보정은 그대로 사용 가능).
- `data/lensfun`, `data/develop_presets`, `data/calibration`, `assets` — exe 옆에
  폴더째 들어갑니다. `appinfo.app_root()`가 exe 위치를 앱 루트로 보므로 다른
  PC에서도 그대로 붙습니다.

전부 `build_windows.py`가 자동으로 포함합니다.

**ONNX 모델은 `--include-package=arw_selector`로 따라오지 않습니다.** 그 옵션은
파이썬 모듈만 가져갑니다. `.onnx`는 데이터라 `DATA_FILES`에 적어야 하는데, 두
모델 모두 없을 때 예외를 던지지 않고 물러서도록 만들어져 있어서 **빌드도 실행도
성공한 채로 기능만 사라집니다.** 모델을 추가하면 `build_windows.py`의
`DATA_FILES`, `verify_dist.py`, `launcher.py`의 자체 점검 세 곳을 함께 고치십시오.

### 백신이 빌드를 막는 경우

다음 오류로 빌드가 실패할 수 있습니다.

```
FATAL: Failed to add resources to file '...\RAW_selector.exe', the result is unusable.
```

컴파일과 링크는 이미 끝난 상태이고, **버전 정보(제품명·설명)를 exe에 써 넣는
마지막 단계**에서만 실패한 것입니다. Nuitka는 링크가 끝난 뒤 exe를 다시 열어
리소스를 기록하는데, 이때 백신이 갓 만들어진 26MB 파일을 검사하느라 파일을
잡고 있으면 열리지 않습니다. Nuitka가 5회 재시도한 뒤 포기합니다.

이 환경에서 실제로 겪은 경우이며, 백신 세 종류(Windows Defender, COMODO,
Bitdefender)가 함께 설치되어 있었습니다. 작은 테스트 exe는 검사가 빨리 끝나
통과하고 큰 exe만 실패하므로, 원인을 짚기 어렵습니다.

`build_windows.py`는 이 오류를 만나면 버전 정보 없이 자동으로 다시 빌드합니다.
실행에는 아무 지장이 없고 exe 속성 창의 제품명·설명만 비게 됩니다.

버전 정보까지 넣으려면 셋 중 하나를 선택하십시오.

**1. 이미 만들어진 exe에 나중에 써 넣기 (권장).** 전체를 다시 빌드할 필요가
없습니다. 백신 검사가 끝난 뒤에 돌리면 됩니다.

```bash
python stamp_version.py <버전정보를_가진.exe> build\launcher.dist\RAW_selector.exe
```

같은 값으로 빌드한 다른 exe에서 VS_VERSIONINFO 블록을 통째로 복사해 옵니다.
바이트 배치를 직접 짜다 틀리면 파일이 깨지는데, 복사는 이미 검증된 블록을
그대로 옮기는 것이라 안전합니다. 원본을 바로 고치지 않고 사본에 쓴 뒤 바꿔치기
하므로, 중간에 실패해도 멀쩡한 exe가 깨지지 않습니다.

**2. 백신 실시간 검사에서 빌드 폴더(`build/`)를 제외한 뒤 다시 빌드.**

**3. 빌드 동안만 실시간 검사를 끄기.**

10분 넘게 걸리는 빌드를 버전 정보 때문에 다시 도는 것은 거의 항상 손해입니다.
1번을 먼저 시도하십시오.

처음부터 버전 정보 없이 빌드하려면 `--no-version-info`를 붙입니다.

```bash
python build_windows.py --no-version-info
```

---

## macOS

macOS와 Xcode가 준비되어 있다면 아래 절차로 빌드합니다. 코드는 이미
크로스플랫폼으로 작성되어 있어 소스 수정이 필요 없습니다.

### 1. 준비

```bash
git clone <저장소 주소> ARW_SELECTOR
cd ARW_SELECTOR

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[gui,lens,dev]"
```

**Homebrew는 필요 없습니다.** 의존성 전부 Apple Silicon 휠이 있습니다.
2026-07-23에 `pip download --platform macosx_14_0_arm64`로 확인한 것 —
Windows에서 쓰는 것과 같은 버전입니다:

| 패키지 | 버전 | 휠 |
|---|---|---|
| PySide6 | 6.11.1 | `macosx_13_0_universal2` |
| opencv-python | 5.0.0.93 | `macosx_13_0_arm64` |
| rawpy | 0.27.0 | `macosx_11_0_arm64` |
| lensfunpy | 1.18.0 | `macosx_14_0_arm64` |
| pillow-heif | 1.5.0 | `macosx_11_0_arm64` |

**macOS 14 이상이어야 합니다.** 13에서는 lensfunpy가 1.15.0으로 내려가고,
11~12에서는 PySide6가 6.7.3까지 내려갑니다. 그 조합은 검증한 적이 없습니다.

빌드까지 할 때만 컴파일러가 필요합니다:

```bash
xcode-select --install
pip install nuitka dmgbuild
```

`dmgbuild`는 배포용 DMG에 Applications 바로가기와 화살표 배경을 넣는 데
씁니다(아래 3절). 없으면 `build_macos.py`가 시작할 때 알려 줍니다.

설치 확인은 자체 점검으로 합니다. 빠진 것이 있으면 여기서 잡힙니다
(모델 파일, 렌즈 DB, 프리셋, HEIF 디코더, 번역, GUI 생성까지 봅니다):

```bash
python launcher.py --selftest
```

### 2. 테스트

```bash
pytest
```

Windows에서 1543개가 통과합니다(실측 19분). macOS에서 실기 검증은 아직 하지 않았으므로
이 단계에서 플랫폼 차이가 드러날 수 있습니다. 특히 다음을 확인하십시오.

- **경로 대소문자**: macOS의 기본 APFS는 대소문자를 **구분하지 않습니다**
  (Windows와 같습니다). 구분하도록 포맷할 수도 있으므로 확장자 비교는 전부
  `.lower()`로 되어 있습니다. 맥에서 새로 나타나는 위험은 반대쪽입니다 —
  `IMG_1.ARW`와 `img_1.arw`가 같은 파일로 취급됩니다.
- **병렬 처리**: macOS는 `spawn` 방식이라 워커에 넘기는 값이 모두 picklable
  해야 합니다. 그렇게 작성되어 있으나 실제 실행으로 확인이 필요합니다.
- **한글 폰트**: 워터마크와 정보 띠는 `/System/Library/Fonts/AppleSDGothicNeo.ttc`
  를 찾습니다.
- **화면 크기**: 창이 맞춰야 하는 기준은 13" MacBook Air (M1)의 기본 Retina인
  1440x900 points입니다. 메뉴 막대와 Dock을 빼면 실사용 1440x806입니다.
  맥의 기본 UI 폰트는 Windows보다 크므로 툴바가 실제로 몇 줄이 되는지
  눈으로 확인하십시오 (`tests/test_toolbar_fits_small_screens.py`가
  자동으로 보긴 합니다).

### 3. 빌드

**권장:** `build_macos.py`가 아래 과정을 한 번에 합니다. 포함할 데이터 목록을
`build_windows.py`에서 그대로 가져다 쓰므로 손으로 옮기다 빠뜨릴 일이 없고
(맥 예시에 `data/translations`가 빠져 번역 없는 번들이 나오던 실수가 있었습니다),
Info.plist에 최소 OS(14.0)와 번들 ID를 박고, 자체 점검까지 돌립니다.

```bash
python make_icon.py                 # icon.icns (한 번만)
python build_macos.py --clean --dmg # .app + 드래그 설치용 .dmg
```

`--dmg`는 Applications 폴더 바로가기와 화살표 배경(`dmgbuild`)을 넣은 배포용
DMG를 만듭니다. 이미 빌드된 `.app`을 DMG로만 다시 묶으려면 `--dmg-only`.

아래는 같은 일을 손으로 하는 방법입니다(참고용).

```bash
python -m nuitka \
  --standalone \
  --macos-create-app-bundle \
  --enable-plugin=pyside6 \
  --assume-yes-for-downloads \
  --include-package=arw_selector \
  --include-module=rawpy --include-module=cv2 --include-module=piexif \
  --include-module=exifread --include-module=yaml --include-module=PIL \
  --include-data-files=arw_selector/core/models/face_detection_yunet_2023mar.onnx=arw_selector/core/models/face_detection_yunet_2023mar.onnx \
  --include-data-files=arw_selector/core/models/face_mesh_192x192.onnx=arw_selector/core/models/face_mesh_192x192.onnx \
  --macos-app-name="RAW_selector" \
  --macos-app-version=0.15.1 \
  --output-dir=build \
  launcher.py
```

결과물은 `build/launcher.app`입니다. 이름을 바꾸려면 폴더째 `RAW_selector.app`
으로 변경합니다.

lensfunpy를 포함하려면 데이터베이스 경로를 추가합니다.

```bash
LENSFUN_DB=$(python -c "import lensfunpy, pathlib; print(pathlib.Path(lensfunpy.__file__).parent / 'db_files')")
# 위 명령에 다음 인자를 추가
--include-data-dir="$LENSFUN_DB"=lensfunpy/db_files
```

`data/` 폴더도 함께 넣어야 렌즈 프로필과 프리셋이 붙습니다. Windows 쪽
`build_windows.py`의 `DATA_DIRS`와 같은 목록입니다.

```bash
--include-data-dir=data/lensfun=data/lensfun \
--include-data-dir=data/develop_presets=data/develop_presets \
--include-data-dir=data/select_presets=data/select_presets \
--include-data-dir=data/calibration=data/calibration \
--include-data-dir=assets=assets
```

빌드가 끝나면 `RAW_selector.app` 안의 실행 파일을 `--selftest`로 한 번 돌려
열 항목이 모두 [OK]인지 확인하십시오.

### 4. 서명 및 공증

서명하지 않은 앱은 Gatekeeper가 차단합니다. 배포 대상에 따라 선택합니다.

**본인 컴퓨터에서만 사용**하는 경우 서명이 필요 없습니다(기본 서명은 ad-hoc).

macOS 15부터 "우클릭 후 열기"로 넘기는 길이 없어졌습니다. 둘 중 하나를 씁니다.

- 한 번 실행해 차단당한 뒤, **시스템 설정 → 개인정보 보호 및 보안**에서
  "확인 없이 열기"를 누릅니다.
- 또는 격리 딱지를 직접 뗍니다:

```bash
xattr -dr com.apple.quarantine build/RAW_selector.app
```

**다른 사람에게 배포**하는 경우 Apple Developer 계정(연 $99)이 필요합니다.

```bash
# 1. 서명 (Developer ID Application 인증서 필요)
codesign --deep --force --options runtime \
  --sign "Developer ID Application: 본인이름 (팀ID)" \
  "build/RAW_selector.app"

# 2. 배포용 이미지 생성
hdiutil create -volname "RAW_selector" \
  -srcfolder "build/RAW_selector.app" \
  -ov -format UDZO "RAW_selector.dmg"

# 3. 공증 (Apple 서버에 제출, 수 분 소요)
xcrun notarytool submit "RAW_selector.dmg" \
  --apple-id "본인@example.com" \
  --team-id "팀ID" \
  --password "앱 암호" \
  --wait

# 4. 공증 결과를 파일에 첨부
xcrun stapler staple "RAW_selector.dmg"
```

`앱 암호`는 Apple ID 계정 페이지에서 발급하는 앱 전용 암호입니다. 계정
비밀번호를 그대로 쓰지 마십시오.

### 5. Apple Silicon / Intel

기본적으로 빌드하는 기계의 아키텍처용으로 만들어집니다. 두 아키텍처를 모두
지원하려면 각각의 기계에서 빌드하거나 유니버설 바이너리를 구성해야 합니다.
Nuitka는 유니버설 바이너리를 직접 만들지 않으므로, 실무적으로는 대상
아키텍처별로 따로 빌드하는 편이 간단합니다.

---

## 배포 전 점검

먼저 자동 점검 두 가지를 돌립니다.

```bash
python -m pytest tests/ -q
python verify_dist.py build\launcher.dist
```

`verify_dist.py`는 파일 구성을 세고, 실제로 exe를 **진짜 콘솔(cmd)을 통해**
`--selftest`로 실행해 봅니다. `capture_output`으로 파이프를 만들어 실행하면
콘솔이 없는 exe도 출력이 잡혀서 "콘솔에서 돌리면 아무것도 안 나온다"는 문제를
못 잡습니다. 실제로 그렇게 놓친 적이 있습니다.

받는 사람 쪽에서는 `자체점검.bat`을 더블클릭하면 같은 검사가 돕니다.
`--selftest`가 세는 열 항목은 버전·경로, 이미지 라이브러리, 얼굴 검출 모델,
얼굴 윤곽 모델, 렌즈 프로필 DB, 보정 프리셋, 판정 프리셋, HEIF 디코더, 번역,
GUI 생성입니다.

**`verify_dist.py`는 갓 빌드한 폴더에 한 번만 돌리십시오.** `--selftest`가 배포
폴더 안에 `data/logs/`를 만들기 때문에, 같은 폴더에 두 번째로 돌리면 "로그
폴더가 섞이지 않음" 항목이 반드시 실패합니다. 배포 전에 그 폴더를 지우거나,
zip을 먼저 만든 뒤 검증하십시오.

그다음 손으로 확인할 것:

| 항목 | 확인 방법 |
|---|---|
| 실행 | 빌드 결과물을 직접 실행해 창이 뜨는지 |
| 얼굴 검출 | 인물 사진 분석 후 판정 기준이 "눈 영역"으로 표시되는지 |
| 얼굴 윤곽 | 보정 창에서 마스크 프리셋 "언더아이 리터치"가 눈 밑에만 걸리는지 |
| 렌즈 보정 | 광학 패널에서 렌즈 이름이 조회되는지 |
| 로그 | `data\logs\raw_selector.log`가 생기는지 |
| 병렬 처리 | 수백 장 분석이 정상 완료되는지 |

로그와 프리셋은 **실행 파일 옆 `data/`** 에 들어갑니다. 앱 폴더를 통째로 옮기면
같이 따라가야 하기 때문입니다. Program Files처럼 쓰기가 막힌 곳에 설치했을
때만 사용자 폴더로 물러섭니다.

- Windows: `%APPDATA%\raw_selector\data\logs\`
- macOS: `~/Library/Application Support/raw_selector/data/logs/`

기기별 상태(마지막으로 연 폴더 등)만 항상 `%APPDATA%\raw_selector\`에 둡니다.
