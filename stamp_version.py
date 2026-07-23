"""빌드된 exe에 버전 정보(제품명·버전·설명)를 나중에 써 넣습니다.

Nuitka는 링크가 끝난 뒤 exe에 리소스를 써 넣는데, 갓 만들어진 27MB짜리
파일을 백신이 검사하며 잡고 있으면 그 단계만 실패합니다. 그러면 빌드는
성공했는데 파일 속성의 제품명·버전이 비어 있게 됩니다.

이 도구는 이미 만들어진 exe에 그 리소스만 따로 넣습니다. 백신 검사가
끝난 뒤에 돌리면 되므로 전체 빌드를 다시 할 필요가 없습니다.

리소스 구조(VS_VERSIONINFO)를 손으로 만들지 않고, 같은 값으로 빌드한
작은 exe에서 통째로 복사해 옵니다 — 바이트 배치를 직접 짜다 틀리면
파일이 깨지는데, 복사는 이미 검증된 블록을 그대로 옮기는 것이라 안전합니다.

    python stamp_version.py <값을_가진.exe> <대상.exe>
"""

from __future__ import annotations

import ctypes
import shutil
import sys
from ctypes import wintypes
from pathlib import Path

RT_VERSION = 16
VERSION_ID = 1
LANG_NEUTRAL = 0

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

kernel32.LoadLibraryExW.restype = wintypes.HMODULE
kernel32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
kernel32.FindResourceW.restype = wintypes.HANDLE
kernel32.FindResourceW.argtypes = [wintypes.HMODULE, wintypes.LPCWSTR, wintypes.LPCWSTR]
kernel32.SizeofResource.restype = wintypes.DWORD
kernel32.SizeofResource.argtypes = [wintypes.HMODULE, wintypes.HANDLE]
kernel32.LoadResource.restype = wintypes.HANDLE
kernel32.LoadResource.argtypes = [wintypes.HMODULE, wintypes.HANDLE]
kernel32.LockResource.restype = ctypes.c_void_p
kernel32.LockResource.argtypes = [wintypes.HANDLE]
kernel32.BeginUpdateResourceW.restype = wintypes.HANDLE
kernel32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
kernel32.UpdateResourceW.restype = wintypes.BOOL
kernel32.UpdateResourceW.argtypes = [
    wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPCWSTR,
    wintypes.WORD, ctypes.c_void_p, wintypes.DWORD,
]
kernel32.EndUpdateResourceW.restype = wintypes.BOOL
kernel32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]

LOAD_LIBRARY_AS_DATAFILE = 0x00000002


def read_version_resource(exe: Path) -> bytes:
    """exe에서 VS_VERSIONINFO 블록을 통째로 읽어 옵니다."""
    handle = kernel32.LoadLibraryExW(str(exe), None, LOAD_LIBRARY_AS_DATAFILE)
    if not handle:
        raise OSError(f"exe를 열지 못했습니다: {ctypes.get_last_error()}")

    found = kernel32.FindResourceW(
        handle, ctypes.cast(VERSION_ID, wintypes.LPCWSTR),
        ctypes.cast(RT_VERSION, wintypes.LPCWSTR),
    )
    if not found:
        raise OSError("버전 리소스가 없습니다")

    size = kernel32.SizeofResource(handle, found)
    loaded = kernel32.LoadResource(handle, found)
    pointer = kernel32.LockResource(loaded)
    if not pointer or not size:
        raise OSError("버전 리소스를 읽지 못했습니다")

    return ctypes.string_at(pointer, size)


def write_version_resource(exe: Path, blob: bytes) -> None:
    """exe에 VS_VERSIONINFO 블록을 써 넣습니다."""
    update = kernel32.BeginUpdateResourceW(str(exe), False)
    if not update:
        raise OSError(
            f"리소스 쓰기를 시작하지 못했습니다 (오류 {ctypes.get_last_error()}). "
            "백신이 파일을 잡고 있을 수 있습니다."
        )

    buffer = ctypes.create_string_buffer(blob, len(blob))
    ok = kernel32.UpdateResourceW(
        update,
        ctypes.cast(RT_VERSION, wintypes.LPCWSTR),
        ctypes.cast(VERSION_ID, wintypes.LPCWSTR),
        LANG_NEUTRAL,
        ctypes.cast(buffer, ctypes.c_void_p),
        len(blob),
    )
    if not ok:
        kernel32.EndUpdateResourceW(update, True)
        raise OSError(f"리소스를 쓰지 못했습니다 (오류 {ctypes.get_last_error()})")

    if not kernel32.EndUpdateResourceW(update, False):
        raise OSError(f"리소스 쓰기를 마치지 못했습니다 (오류 {ctypes.get_last_error()})")


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    source, target = Path(sys.argv[1]), Path(sys.argv[2])
    for path in (source, target):
        if not path.is_file():
            print(f"파일이 없습니다: {path}")
            return 1

    blob = read_version_resource(source)
    print(f"가져온 버전 리소스: {len(blob)} 바이트  ({source.name})")

    # 원본을 바로 고치지 않습니다. 중간에 실패하면 멀쩡한 exe가 깨집니다.
    staging = target.with_suffix(target.suffix + ".stamping")
    shutil.copy2(target, staging)
    try:
        write_version_resource(staging, blob)
    except OSError:
        staging.unlink(missing_ok=True)
        raise

    backup = target.with_suffix(target.suffix + ".bak")
    backup.unlink(missing_ok=True)
    target.rename(backup)
    staging.rename(target)
    backup.unlink(missing_ok=True)

    print(f"써 넣었습니다: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
