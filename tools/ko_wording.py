"""Korean wording for the UI, keyed by the English source string.

This is the working copy that `fill_translations.py` pours into the `.ts`
file. Qt Linguist can edit the `.ts` directly, but going through a plain
dict keeps the wording reviewable in a normal diff — a `.ts` diff is
mostly XML noise.

**The wording here is what users already saw.** This whole exercise
changes the container, not the words: anyone who used the Korean build
should not notice a single phrase has moved.
"""

WORDING = {
    # ---------------------------------------------------------- score card
    "Analysis failed": "분석 실패",
    "Sharpness": "선명도",
    "Focus missed the face": "얼굴 초점 빗나감",
    "Focus on the face": "얼굴에 초점 맞음",
    "No face": "얼굴 없음",
    "Face detected": "얼굴 검출",
    "Face size": "얼굴 크기",
    "Eyes detected": "눈 검출",
    "Eyes closed": "눈 감김",
    "Eyes open": "눈 뜸",
    "Eyes not measured": "눈 못 잼",
    "Blown highlights": "하이라이트 날아감",
    "Crushed shadows": "섀도우 뭉개짐",
    "Lens cap / stray shutter": "렌즈캡/오발 셔터",
    "Clamped to range": "범위 제한",
    "Total": "합계",
    "score {score:.1f}": "점수 {score:.1f}",
    "eye area": "눈 영역",
    "face area": "얼굴 영역",
    "estimated subject": "피사체 추정",
    "whole frame": "전체 프레임",
    "{roi_name} {roi:.0f} × trust {trust:.2f} + frame {frame:.0f} "
    "× {frame_weight:.2f}, ×{scale:g}":
        "{roi_name} {roi:.0f} × 신뢰도 {trust:.2f} + 전체 {frame:.0f} "
        "× {frame_weight:.2f}, ×{scale:g}",
    "background {background:.0f} > face {face:.0f}":
        "배경 {background:.0f} > 얼굴 {face:.0f}",
    "face {area:.2f}% of {threshold:.1f}% → ×{weight:.2f}":
        "얼굴 {area:.2f}% / 기준 {threshold:.1f}% → 배율 {weight:.2f}",
    "EAR {ear:.2f} < threshold {threshold:.2f}":
        "EAR {ear:.2f} < 임계 {threshold:.2f}",
    "EAR {ear:.2f} ≥ threshold {threshold:.2f}":
        "EAR {ear:.2f} ≥ 임계 {threshold:.2f}",
    "{clipped:.1f}% (allowed {allowed:.1f}%)": "{clipped:.1f}% (허용 {allowed:.1f}%)",
    "mean brightness {luma:.0f}": "평균 밝기 {luma:.0f}",
    "{total:.1f} clamped into 0–100": "합계 {total:.1f} → 0~100으로 자름",

    # ---------------------------------------------------------- reasons
    "sharpness {sharpness:.0f} on the {roi_name}":
        "{roi_name} 기준 선명도 {sharpness:.0f}",
    "{count} face(s)": "얼굴 {count}개",
    "focus missed the face (background sharper by {deficit:.0f})":
        "얼굴 초점 빗나감 (배경이 {deficit:.0f}점 더 선명)",
    "highlights {percent:.0f}% clipped": "하이라이트 {percent:.0f}% 클리핑",
    "shadows {percent:.0f}% crushed": "섀도우 {percent:.0f}% 뭉개짐",
    "eyes not measured (profile, occluded or too small)":
        "눈 못 잼 (옆얼굴·가림·너무 작음)",
    "eyes look closed (EAR {ear:.2f} < {threshold:.2f})":
        "눈 감김 의심 (EAR {ear:.2f} < {threshold:.2f})",
    "eyes open (EAR {ear:.2f})": "눈 뜸 (EAR {ear:.2f})",
    "frame is almost black": "프레임이 거의 검음",
    "frame is almost white": "프레임이 거의 흼",
    "bottom of the batch (below {threshold:.0f})": "배치 하위 (기준 {threshold:.0f})",
    "a shot {deficit:.0f} points better exists in this scene":
        "같은 장면에 {deficit:.0f}점 더 나은 컷 있음",
    "{format} source — less latitude than RAW":
        "{format} 원본 — RAW보다 보정 여유가 적음",

    # ---------------------------------------------------------- filter bar
    "All": "전체",
    "All {total}": "전체 {total}",
    "Scenes with no keep": "keep 없는 장면",
    "Scenes with no keep {count}": "keep 없는 장면 {count}",
    "Scenes that produced no keep at all — either scene\n"
    "guarantees are off, or every shot fell below the\n"
    "quality floor. Check here for anything missed.":
        "장면 보장을 껐거나 품질 하한에 걸려\n"
        "keep이 하나도 나오지 않은 장면들입니다.\n"
        "놓친 컷이 없는지 여기서 확인합니다.",

    # ---------------------------------------------------------- presets
    "Save": "저장",
    "Import": "가져오기",
    "Export": "내보내기",
    "Delete": "삭제",
    "Load": "불러오기",
    "Apply": "적용",
    "Name": "이름",
    "New name": "새 이름",
    "Preset": "프리셋",
    "(unsaved)": "(저장 안 됨)",
    "Save preset": "프리셋 저장",
    "Import preset": "프리셋 가져오기",
    "Export preset": "프리셋 내보내기",
    "Delete preset": "프리셋 삭제",
    "Save the current settings as a preset": "현재 설정을 프리셋으로 저장합니다",
    "Load a preset file (.yaml) into the list.\n"
    "Presets from another machine or folder work as they are.":
        "프리셋 파일(.yaml)을 불러와 목록에 추가합니다.\n"
        "다른 PC나 다른 폴더의 프리셋을 그대로 쓸 수 있습니다.",
    "Write the selected preset to a file, for backup or sharing":
        "선택한 프리셋을 파일로 저장합니다 (백업·공유용)",
    "Could not load:\n{error}": "불러오지 못했습니다:\n{error}",
    "Could not save:\n{error}": "저장하지 못했습니다:\n{error}",
    "'{name}' already exists. Overwrite?": "'{name}' 이(가) 이미 있습니다. 덮어쓸까요?",
    "'{name}' already exists. Overwrite?\n"
    "Choose No to save it under a different name.":
        "'{name}' 이(가) 이미 있습니다. 덮어쓸까요?\n"
        "아니오를 누르면 다른 이름으로 저장합니다.",
    "{name} copy": "{name} 사본",
    "Presets (*.yaml *.yml);;All files (*)": "프리셋 (*.yaml *.yml);;모든 파일 (*)",
    "Presets (*.yaml)": "프리셋 (*.yaml)",
    "The source file is missing.": "원본 파일을 찾을 수 없습니다.",
    "Delete '{name}'?": "'{name}' 을(를) 지울까요?",

    # ---------------------------------------------------------- queue
    "Queue": "대기열",
    "Queue ({count})": "대기열 ({count})",
    "File": "파일",
    "Develop preset": "보정 프리셋",
    "Crop": "크롭",
    "Grade": "등급",
    "(per-photo edit)": "(개별 보정)",
    "(no edit)": "(보정 없음)",
    "Remove selected": "선택 제거",
    "Clear": "비우기",
    "Export queue": "대기열 내보내기",
    "Save queue": "대기열 저장",
    "Load queue": "대기열 불러오기",
    "Selected rows:": "선택 항목에",
    "Double-click to edit in the develop window": "더블클릭하면 보정 화면에서 편집합니다",
    "Save the queue to a file and pick it up next session":
        "대기열을 파일로 저장해 다음 세션에서 이어서 씁니다",
    "Set the develop preset on the selected rows":
        "선택한 행의 보정을 이 프리셋으로 바꿉니다",
    "\n⚠ source is missing — it will be skipped on export":
        "\n⚠ 원본이 없습니다 — 내보낼 때 건너뜁니다",
    "This photo has a crop or straighten applied": "이 컷에 크롭/기울이기가 걸려 있습니다",
    "No crop": "크롭 없음",
    "{count} photos · {developed} edited · {cropped} cropped":
        "{count}장 · 보정 {developed}장 · 크롭 {cropped}장",
    "\n⚠ {count} with a missing source will be skipped":
        "\n⚠ 원본이 사라진 {count}개는 건너뜁니다",
    "Source is missing:\n{path}": "원본이 없습니다:\n{path}",
    "Select some rows first": "먼저 행을 선택하십시오",
    "Nothing to save": "저장할 항목이 없습니다",
    "Clear all {count} entries?": "{count}개 항목을 모두 비울까요?",
    "{added} added, {updated} updated": "추가 {added}개, 갱신 {updated}개",

    # ---------------------------------------------------------- settings panel
    "Restore defaults": "기본값으로",
    "Keep criteria": "keep 기준",
    "Reject criteria": "reject 기준",
    "Score weights": "점수 가중치",
    "Scene splitting": "장면 나누기",
    " pts": " 점",
    " photos": " 장",
    " s": " 초",

    "Aim for a target ratio": "목표 비율로 자동 조정",
    "An absolute score means something different in every batch.\n"
    "Given a ratio, the threshold is derived from that batch's own\n"
    "score distribution, so the result holds across shoots.":
        "절대 점수는 배치마다 의미가 달라집니다.\n"
        "비율을 주면 배치 점수 분포에서 임계값을 역산하므로\n"
        "촬영이 바뀌어도 결과 비율이 유지됩니다.",
    "Target keep ratio": "목표 keep 비율",
    "At or above this score, keep regardless of rank":
        "이 점수 이상이면 순위와 무관하게 keep",
    "Absolute keep score": "keep 절대 점수",
    "no guarantee": "보장 안 함",
    "How many top shots to keep per scene.\n"
    "0 turns the scene guarantee off — grading is then purely by\n"
    "score, so some scenes may end up with no keep at all.":
        "장면마다 남길 상위 컷 수.\n"
        "0이면 장면 보장을 끕니다 — 점수만으로 판정하므로\n"
        "keep이 하나도 없는 장면이 생길 수 있습니다.",
    "Keeps per scene": "장면당 keep",
    "Minimum score a shot needs before it can be promoted to keep.\n"
    "At 0 every scene yields at least one photo.\n"
    "Above 0, a scene where nothing reaches this score yields\n"
    "nothing at all.":
        "keep으로 올리기 위한 최소 점수.\n"
        "0이면 모든 장면에서 최소 1장이 반드시 나옵니다.\n"
        "0보다 크면 장면 전체가 이 점수에 못 미칠 때\n"
        "그 장면에서는 아무것도 뽑지 않습니다.",
    "Keep quality floor": "keep 품질 하한",
    "Not analysed yet — the distribution appears after analysis":
        "분석 전 — 분석하면 점수 분포가 표시됩니다",
    "{count} photos · min {low:.1f} / mean {mean:.1f} / max {high:.1f}\n"
    "target {ratio:.0f}% → cuts at about {cutoff:.1f}":
        "{count}장 · 최소 {low:.1f} / 평균 {mean:.1f} / 최대 {high:.1f}\n"
        "목표 {ratio:.0f}% → 약 {cutoff:.1f}점에서 잘립니다",

    "Save to file": "파일로 저장",
    "Write the current grading criteria to a YAML file":
        "현재 판정 기준을 YAML 파일로 저장합니다",
    "Load from file": "파일에서 불러오기",
    "Save grading criteria": "판정 기준 저장",
    "Load grading criteria": "판정 기준 불러오기",
    "criteria.yaml": "판정기준.yaml",
    "Grading criteria": "판정 기준",
    "Save failed": "저장 실패",
    "Load failed": "불러오기 실패",
    "Saved to:\n{path}": "저장했습니다:\n{path}",
    "Loaded from:\n{path}": "불러왔습니다:\n{path}",
    "Not a grading criteria file.": "판정 기준 파일이 아닙니다.",

    "In face-priority mode sharpness is multiplied by {scale:g}.\n"
    "Letting sharpness alone use the full 0–100 means any bonus at\n"
    "all pins the score to 100, every good shot ends up with the\n"
    "same number, and the ranking disappears.\n\n"
    "So sharpness uses 0–{half:g} and the face and eye signals use\n"
    "the rest.\n\n"
    "Turning the mode off removes those signals, so the multiplier\n"
    "becomes {full:g} — otherwise the top half of the range sits\n"
    "empty and nothing reaches the keep threshold (measured: 45.1\n"
    "max across 2845 A6700 frames).\n\n"
    "The absolute thresholds below (keep score, reject floor)\n"
    "assume this scale.":
        "얼굴 우선 모드에서는 선명도에 {scale:g}을 곱합니다.\n"
        "선명도만으로 0~100을 다 쓰면 보너스를 조금만 켜도 곧바로\n"
        "100에 붙어, 잘 찍은 컷이 전부 같은 점수가 되고 순위가\n"
        "사라집니다.\n\n"
        "그래서 선명도는 0~{half:g}을 쓰고 나머지를 얼굴·눈 신호가\n"
        "씁니다.\n\n"
        "모드를 끄면 그 신호가 전부 빠지므로 배수가 {full:g}이 됩니다 —\n"
        "안 그러면 상단 절반이 비어 keep 기준에 닿는 컷이 없습니다\n"
        "(실측 A6700 2845장에서 최대 45.1점).\n\n"
        "아래 절대 점수 임계값(keep 절대 점수, reject 절대 하한)이\n"
        "이 척도를 전제로 한 값입니다.",
    "score = (ROI sharpness × trust\n     + frame sharpness × (1 − trust))\n":
        "점수 = (ROI 선명도 × 신뢰도\n     + 전체 선명도 × (1 − 신뢰도))\n",
    "     × {scale:g} + bonuses − penalties": "     × {scale:g} + 보너스 − 감점",
    "     × {scale:g}  (face priority off — no face or eye terms)":
        "     × {scale:g}  (얼굴 우선 꺼짐 — 얼굴·눈 항목 없음)",

    "Face-priority mode": "얼굴 우선 모드",
    "Trusts the face and eye regions on shots where a face was\n"
    "found, and penalises shots where the face is soft but the\n"
    "background is sharper (focus fell behind the subject).\n"
    "Turn it off for landscape work to grade on whole-frame\n"
    "sharpness alone.":
        "얼굴을 잡은 컷에서 얼굴·눈 영역을 더 신뢰하고,\n"
        "얼굴은 흐린데 배경이 더 선명한 컷(초점이 뒤로 빠짐)을\n"
        "감점합니다.\n"
        "풍경 위주라면 꺼서 전체 프레임 선명도로만 판정합니다.",
    "Largest penalty when the background is sharper than the face.\n"
    "Scales with the gap and with the face detector's confidence.":
        "배경이 얼굴보다 선명할 때의 최대 감점.\n"
        "격차가 클수록, 얼굴 검출 신뢰도가 높을수록 크게 깎입니다.",
    "  Focus missed the face": "  얼굴 초점 빗나감 감점",
    "Added when the focus ROI really is a face or a pair of eyes.\n"
    "'A face is in the frame' and 'the focus landed on that face'\n"
    "are different things — this bonus is only for the second.":
        "초점 ROI가 실제로 얼굴·눈일 때 더하는 점수.\n"
        "'얼굴이 화면에 있다'와 '그 얼굴에 초점이 맞았다'는 다릅니다 —\n"
        "이 보너스는 후자에만 붙습니다.",
    "  Focus on the face": "  얼굴에 초점 맞음 보너스",
    "Penalty for a shot with no face at all, in face-priority mode.\n\n"
    "Measured across 2845 A6700 frames: faceless shots had a median\n"
    "score of 59.0 against 47.6 for shots focused on a face. Face\n"
    "shots are measured on the softer face ROI and can pick up the\n"
    "background-focus penalty, while faceless shots use frame\n"
    "sharpness with nothing deducted. This levels the two groups so\n"
    "they can be compared.":
        "얼굴 우선 모드인데 얼굴이 하나도 없을 때의 감점.\n\n"
        "실측(A6700 2845장): 얼굴 없는 컷의 점수 중앙값이 59.0으로,\n"
        "초점이 얼굴에 맞은 컷의 47.6보다 오히려 높았습니다.\n"
        "얼굴 컷은 더 부드러운 얼굴 ROI로 재고 배경초점 감점까지 받는데,\n"
        "얼굴 없는 컷은 프레임 선명도를 감점 없이 쓰기 때문입니다.\n"
        "두 집단을 견줄 수 있게 맞추는 보정입니다.",
    "  No face": "  얼굴 없음 감점",

    "Eye state — sharpness cannot catch this": "눈 상태 — 초점으로는 걸러지지 않는 항목",
    "Open eyes add, closed eyes subtract. The real gap between an\n"
    "open-eyed and a closed-eyed shot is the sum of the two.\n\n"
    "Shots where the eyes could not be measured (profile, occluded)\n"
    "get neither — an unknown is treated as neither good nor bad.":
        "뜨면 가산, 감으면 감점입니다.\n"
        "뜬 컷과 감은 컷의 실제 점수 차이는 두 값의 합입니다.\n\n"
        "눈을 못 잰 컷(옆얼굴·가림)은 어느 쪽도 받지 않습니다 —\n"
        "모르는 것을 좋게도 나쁘게도 보지 않습니다.",
    "Added when the main subject's eyes look open.\n\n"
    "With only a penalty, 'eyes open' and 'eyes not measured' score\n"
    "identically. A profile shot nobody could measure would then be\n"
    "treated like a subject looking straight at the camera, and the\n"
    "single most useful signal in portrait selection is half wasted.":
        "주 피사체가 눈을 뜬 것으로 보일 때의 가산.\n\n"
        "감점만 있으면 '눈을 떴다'와 '눈을 못 쟀다'가 점수에서 같습니다.\n"
        "옆얼굴이라 못 잰 컷과 정면으로 눈을 뜬 컷이 같은 대우를 받아,\n"
        "인물 셀렉트에서 제일 중요한 신호가 반만 쓰입니다.",
    "  Eyes open": "  눈 뜸 보너스",
    "Penalty when the main subject's eyes look closed.\n"
    "Closed eyes are still in focus, so sharpness never catches them.\n\n"
    "Size it to push the shot out of automatic keep, rather than all\n"
    "the way down into reject.":
        "주 피사체가 눈을 감은 것으로 보일 때의 감점.\n"
        "감은 눈도 초점은 맞아 있어서 선명도로는 전혀 안 걸러집니다.\n\n"
        "reject로 떨어뜨리기보다 자동 keep에서 밀어내는 크기로 두십시오.",
    "  Eyes closed": "  눈 감김 감점",
    "Eyes count as closed below this eye aspect ratio (EAR).\n\n"
    "Measured on 107 hand-labelled photos (28 closed / 79 open) —\n"
    "caught / falsely penalised:\n"
    "  0.22 —  14/28  ·   2/79   (85% correct)\n"
    "  0.25 —  17/28  ·   7/79   (83% correct)\n"
    "  0.28 —  24/28  ·  16/79   (81% correct)\n"
    "  0.30 —  25/28  ·  19/79   (79% correct)  (default)\n"
    "  0.32 —  25/28  ·  26/79   (73% correct)\n"
    "  0.35 —  26/28  ·  40/79   (61% correct)\n\n"
    "There is no reason to go above 0.30 — 0.32 catches the same\n"
    "number while penalising seven more good shots. Closed eyes sit\n"
    "mostly below 0.28, open eyes start at 0.20, and above that the\n"
    "two distributions only overlap.\n\n"
    "Shots where the eyes could not be measured are never penalised,\n"
    "at any value.":
        "눈 종횡비(EAR)가 이 값보다 작으면 감았다고 봅니다.\n\n"
        "사용자 라벨 107장(감음 28 / 뜸 79) 실측 — 잡아냄 / 거짓감점:\n"
        "  0.22 —  14/28  ·   2/79   (정확 85%)\n"
        "  0.25 —  17/28  ·   7/79   (정확 83%)\n"
        "  0.28 —  24/28  ·  16/79   (정확 81%)\n"
        "  0.30 —  25/28  ·  19/79   (정확 79%)  (기본)\n"
        "  0.32 —  25/28  ·  26/79   (정확 73%)\n"
        "  0.35 —  26/28  ·  40/79   (정확 61%)\n\n"
        "0.30 위로는 올릴 이유가 없습니다 — 0.32는 잡는 수가 같은데\n"
        "멀쩡한 컷만 7장 더 깎습니다. 감음은 대부분 0.28 이하에 몰려\n"
        "있고 뜬 눈은 0.20부터 시작해 그 위에서 두 분포가 겹칩니다.\n\n"
        "눈을 못 잰 컷은 어느 값에서도 감점하지 않습니다.",
    "  Eyes-closed threshold (EAR)": "  눈 감김 임계 (EAR)",

    "ROI trust — how much to believe the region": "ROI 신뢰도 — 판정 영역을 얼마나 믿을지",
    "This is the 'trust' term in the formula above.\n"
    "Near 1 grades on the ROI's sharpness alone; near 0 grades on\n"
    "the whole frame.\n\n"
    "Face-priority mode does not touch these — it works purely\n"
    "through bonuses and penalties.":
        "위 공식의 '신뢰도' 자리에 그대로 들어가는 값입니다.\n"
        "1에 가까울수록 ROI 선명도만 보고, 0에 가까울수록 전체\n"
        "프레임을 봅니다.\n\n"
        "얼굴 우선 모드는 이 값을 건드리지 않습니다 — 가감점으로만\n"
        "작용합니다.",
    "Eye": "눈 기준",
    "Face": "얼굴 기준",
    "Estimated subject": "피사체 추정",
    "Whole frame": "전체 프레임",
    "With the eyes found, the sharpness inside them is the answer":
        "눈을 잡았으면 그 안의 선명도가 곧 판정 근거입니다",
    "A face was found but the eye ROI was too small":
        "얼굴은 잡았지만 눈 ROI가 작을 때",
    "No face, so the subject was guessed from tiles — trust less":
        "얼굴이 없어 격자로 추정 — 덜 신뢰",
    "No ROI could be found": "ROI를 못 잡았을 때",

    "Bonuses": "보너스",
    "Penalties": "감점",
    "Favour shots with a face. Raise it for portrait work.\n\n"
    "Small faces do not receive all of it — the detector finds\n"
    "audience faces a few dozen pixels across. 'Face size for\n"
    "full bonus' below sets where the full amount starts.":
        "얼굴이 잡힌 컷을 우대합니다. 인물 위주면 올리십시오.\n\n"
        "작은 얼굴은 이 값을 다 받지 못합니다 — 검출기는 수십 화소짜리\n"
        "관객 얼굴도 찾아내기 때문입니다. 아래 '보너스 기준 얼굴 크기'로\n"
        "어디부터 온전히 줄지 정합니다.",
    "Added on top when the eyes were found as well.\n"
    "Scaled by face size the same way as the face bonus.":
        "눈까지 잡혔을 때 추가 가산.\n"
        "얼굴 검출 보너스와 같은 크기 보정을 받습니다.",
    "Favour larger faces, i.e. the actual subject.\n"
    "Separate from the size scaling on the two bonuses above; "
    "this pushes big faces further up.":
        "얼굴이 클수록 가산 (주 피사체 우대).\n"
        "위 두 보너스의 크기 보정과 별개로, 큰 얼굴을 더 밀어 줍니다.",
    "Face size at which the face bonus is paid in full, as a share\n"
    "of the frame area. Smaller faces receive proportionally less,\n"
    "and very small ones receive nothing.\n\n"
    "Roughly, on 26MP (6240×4168):\n"
    "  0.1% — 160×160 px (someone standing far off)\n"
    "  3%   — 880×880 px, head-and-shoulders portrait  (default)\n"
    "  20%  — a close-up filling much of the frame\n\n"
    "Raise it to only credit large faces, which helps on stage work\n"
    "where the audience keeps getting detected. Lower it to credit\n"
    "distant subjects too.\n\n"
    "Across 2845 frames of 300mm stage work the main subject's face\n"
    "had a median of 0.34% and a maximum of 2.99%. For that kind of\n"
    "shoot, drop this to around 0.3%.":
        "얼굴 보너스를 온전히 받기 시작하는 얼굴 크기 (프레임 면적 대비).\n"
        "그보다 작은 얼굴은 크기에 비례해 덜 받고, 아주 작으면 못 받습니다.\n\n"
        "26MP(6240×4168) 기준 대략:\n"
        "  0.1% — 160×160 화소 (멀리 선 사람)\n"
        "  3%   — 880×880 화소, 상반신 인물컷  (기본)\n"
        "  20%  — 얼굴이 화면을 크게 채우는 클로즈업\n\n"
        "올리면 크게 잡힌 얼굴만 인정합니다 — 객석·행인이 섞이는\n"
        "무대 촬영에서 유용합니다. 내리면 멀리 있는 인물도 인정합니다.\n\n"
        "300mm 무대 촬영 2845장에서는 주 피사체 얼굴이 중앙값 0.34%,\n"
        "최대 2.99%였습니다. 그런 촬영이면 0.3% 근처로 내리십시오.",
    "  Face size for full bonus": "  보너스 기준 얼굴 크기",

    "Largest penalty once clipping passes the tolerance":
        "임계를 넘게 클리핑됐을 때 최대 감점",
    "0 by default — deliberately low-key work is common enough "
    "that penalising it does more harm than good":
        "기본 0 — 의도적인 저조도 촬영이 많아 감점하지 않습니다",
    "The frame is almost entirely black or white": "프레임이 거의 검거나 흴 때",
    "Penalties start once more than this fraction is blown":
        "하이라이트가 이 비율을 넘게 날아가면 감점 시작",
    "  Highlight tolerance": "  하이라이트 허용치",
    "  Shadow tolerance": "  섀도우 허용치",

    "Falling this far below the best shot in the same scene counts\n"
    "as a duplicate and is dropped. Raising it leaves more in\n"
    "review; lowering it rejects more.":
        "같은 장면의 베스트보다 이만큼 낮으면 중복으로 보고 버립니다.\n"
        "값을 키우면 review가 늘고, 줄이면 reject가 늡니다.",
    "Gap to the scene's best": "장면 베스트와의 격차",
    "Below this score, always reject (absolute floor)":
        "이 점수 미만은 무조건 reject (절대 하한)",
    "Absolute floor": "절대 하한",
    "What share of the batch's bottom end to treat as reject candidates":
        "배치 하위 몇 %를 reject 후보로 볼지",
    "Batch bottom percentile": "배치 하위 백분위",
    "The best shot in a scene is never rejected, whatever these say":
        "장면 1등은 어떤 값에서도 reject되지 않습니다",

    "A longer gap than this starts a new scene (primary signal)":
        "이 이상 벌어지면 다른 장면 (주 신호)",
    "Scene gap": "장면 분리 간격",
    "How much the picture must change to split a scene (of 64 bits).\n"
    "Higher (40) for telephoto and moving subjects; lower (16–24)\n"
    "for still life and portraits.":
        "화면 변화로 장면을 나누는 기준 (64비트 중).\n"
        "망원·동체 촬영은 높게(40), 정물·인물은 낮게(16~24).",
    "Scene change distance": "화면 전환 거리",
    "Picture-change threshold used only when EXIF has no capture\n"
    "time. With no clock to go on the picture is the only evidence,\n"
    "so it has to be stricter.":
        "EXIF 촬영 시각이 없을 때만 쓰는 화면 변화 임계값.\n"
        "그때는 화면 변화가 유일한 근거라 더 조여야 합니다.",
    "Distance without a time": "시각 없을 때 거리",
    "Force a split once a scene grows past this":
        "한 장면이 이보다 커지면 강제로 끊습니다",
    "Largest scene": "장면 최대 크기",

    "{scenes} scenes / {total} photos\n"
    "With these settings the keep floor is {floor:.1f}%.":
        "장면 {scenes}개 / 총 {total}장\n"
        "현재 설정에서 keep 하한은 {floor:.1f}%.",
    "\n⚠ The target is below the floor, so the floor applies.":
        "\n⚠ 목표가 하한보다 낮아 하한이 적용됩니다.",
    "The quality floor leaves {count} scenes with no keep. "
    "Go through review for those.":
        "품질 하한 때문에 {count}개 장면에서 keep이 나오지 않습니다. "
        "그만큼 review를 꼭 확인하십시오.",

    # ---------------------------------------------------------- main window
    "Sort": "정렬",
    "By filename": "파일순",
    "Highest score first": "점수 높은순",
    "Lowest score first": "점수 낮은순",
    "Sorting by score ignores scenes and lines the whole batch up":
        "점수순은 장면을 무시하고 배치 전체를 한 줄로 세웁니다",

    "Open a folder to begin": "폴더를 열어 시작합니다",
    "Stop": "중단",
    "Stopping…": "중단 중…",
    "Stop the running task (Esc)": "진행 중인 작업을 멈춥니다 (Esc)",
    "about {value:.0f}s left": "약 {value:.0f}초 남음",
    "about {value:.0f} min left": "약 {value:.0f}분 남음",
    "about {value:.1f} h left": "약 {value:.1f}시간 남음",

    "Open folder": "폴더 열기",
    "Open files": "파일 열기",
    "Open one file or a handful, rather than a whole folder.\n"
    "Reads every RAW format: ARW, CR3, NEF, RAF, ORF, RW2, DNG.":
        "파일 하나 또는 여럿만 골라서 엽니다.\n"
        "ARW·CR3·NEF·RAF·ORF·RW2·DNG 등 RAW 포맷을 모두 엽니다.",
    "Analyse": "분석",
    "Include subfolders": "하위 폴더 포함",
    "Criteria ▸": "판정 기준 ▸",
    "Criteria ◂": "판정 기준 ◂",
    "Develop": "보정",
    "Adjust the selected photo in the preview (D)\n"
    "Select several to apply the same edit to all of them":
        "선택한 사진의 보정을 미리보기에서 맞춥니다 (D)\n"
        "여러 장을 선택하면 한 번에 적용됩니다",
    "Add to queue": "대기열 담기",
    "Stack the selected photos, with their current edit, on the queue (Q)\n"
    "Gather across folders and export in one go":
        "선택한 사진을 현재 보정과 함께 대기열에 쌓습니다 (Q)\n"
        "여러 폴더에서 모은 뒤 한 번에 내보낼 수 있습니다",
    "Queue ▸": "대기열 ▸",
    "Queue ◂": "대기열 ◂",
    "Queue {count} {arrow}": "대기열 {count} {arrow}",
    "Queue {arrow}": "대기열 {arrow}",
    "Double-click": "더블클릭",
    "Preview": "미리보기",
    "Double-click opens the embedded JPEG straight away — fast, with "
    "the camera's own colour":
        "더블클릭하면 내장 JPEG으로 즉시 봅니다 (빠름, 색은 카메라 렌더)",
    "Double-click demosaics the RAW for accurate colour and tone":
        "더블클릭하면 RAW를 디모자이크해 정확한 색·계조로 보정합니다",
    "Size": "크기",
    "Undo": "되돌리기",
    "Cache": "캐시",
    "Inspect and clear the cache": "캐시 상태를 보고 지웁니다",
    "Colour calibration": "색 보정",
    "Compare this folder's photos against the camera's own JPEGs to\n"
    "work out colour corrections. The result takes precedence over\n"
    "the library's defaults.":
        "이 폴더의 사진을 카메라 내장 JPEG과 비교해 색 보정값을 계산합니다.\n"
        "라이브러리 기본 색보다 이 PC의 측정값을 우선하게 됩니다.",

    "Choose a RAW folder": "RAW 폴더 선택",
    "Choose RAW files": "RAW 파일 선택",
    "All files (*)": "모든 파일 (*)",
    "{folder} — press Analyse to start": "{folder} — 분석을 누르면 시작합니다",
    "{count} files selected — press Analyse to start":
        "{count}개 파일 선택 — 분석을 누르면 시작합니다",
    "Preparing to analyse…": "분석 준비 중…",
    "Analysing {done}/{total} (cached {cached}, failed {failed})":
        "분석 중 {done}/{total} (캐시 {cached}, 실패 {failed})",
    "Stopping analysis — finishing the photo in progress…":
        "분석 중단 요청 — 진행 중인 장을 마치는 중…",
    "Stopping export — finishing the photo in progress…":
        "내보내기 중단 요청 — 진행 중인 장을 마치는 중…",
    "The task failed": "작업이 실패했습니다",
    "Failed": "실패",

    "Open and analyse a photo folder first.": "먼저 사진 폴더를 열고 분석하십시오.",
    "No usable samples were found in this folder.\n\n"
    "It needs at least {count} photos from the same camera,\n"
    "each carrying the camera's embedded preview.":
        "이 폴더에서 보정에 쓸 표본을 찾지 못했습니다.\n\n"
        "같은 기종의 사진이 {count}장 이상 있어야 하고,\n"
        "각 파일에 카메라 내장 미리보기가 들어 있어야 합니다.",

    "{total} photos · {scenes} scenes · "
    "keep {keep} / review {review} / reject {reject}":
        "{total}장 · {scenes}개 장면 · "
        "keep {keep} / review {review} / reject {reject}",
    " · {count} failed to analyse": " · 분석 실패 {count}",
    "Cancelled — results so far: ": "중단됨 — 여기까지의 결과: ",
    "keep {keep} / review {review} / reject {reject} · {developed} edited":
        "keep {keep} / review {review} / reject {reject} · 보정 {developed}장",
    "{name} — re-graded with the new main subject "
    "(score {score:.1f}, {grade})":
        "{name} — 주 피사체를 바꿔 다시 판정했습니다 (점수 {score:.1f}, {grade})",

    "Grades cannot be changed during an export":
        "내보내는 중에는 등급을 바꿀 수 없습니다",
    "Export in progress — develop and grading are locked until it finishes.":
        "내보내는 중입니다 — 끝날 때까지 보정과 등급을 잠갔습니다.",
    "Select some photos first": "먼저 사진을 선택하십시오",

    "Queued {added} / updated {updated} · {total} in the queue":
        "대기열 추가 {added} / 갱신 {updated} · 현재 {total}장",
    "{count} added to the queue": "대기열에 {count}장 추가",
    ", {count} updated": ", {count}장 갱신",
    " · {total} in the queue": " · 현재 {total}장",
    "Export to": "내보낼 위치",
    "Export the queue to": "대기열을 내보낼 위치",
    "Export to (choosing the source folder creates it inside)":
        "내보낼 위치 (원본 폴더를 고르면 그 안에 만듭니다)",
    "{count} with a missing source will be skipped.":
        "원본이 사라진 {count}장은 건너뜁니다.",

    "{path}\n\nNo cache here.": "{path}\n\n캐시가 없습니다.",
    "Clear cache": "캐시 삭제",
    "{path}\n\n"
    "Analysis results: {entries} ({analysis_mb:.1f}MB)\n"
    "Thumbnails: {thumbs} ({thumb_mb:.1f}MB)\n"
    "Total {total_mb:.1f}MB\n\n"
    "Clearing means the next analysis rebuilds it "
    "({entries} photos / {rebuild}).\n"
    "Undo records are kept.\n\n"
    "Clear the cache?":
        "{path}\n\n"
        "분석 결과 {entries}건 ({analysis_mb:.1f}MB)\n"
        "썸네일 {thumbs}개 ({thumb_mb:.1f}MB)\n"
        "합계 {total_mb:.1f}MB\n\n"
        "캐시를 지우면 다음 분석 때 다시 만듭니다 "
        "({entries}장 / {rebuild}).\n"
        "되돌리기 기록은 지우지 않습니다.\n\n"
        "캐시를 지우시겠습니까?",
    "Cache cleared: {entries} results, {thumbs} thumbnails, {mb:.1f}MB freed":
        "캐시 삭제: 분석 {entries}건, 썸네일 {thumbs}개, {mb:.1f}MB 확보",
    "Cache {mb:.0f}MB": "캐시 {mb:.0f}MB",
    "No cache": "캐시 없음",

    "An export is already running.\n\n"
    "Start again once it finishes or is cancelled.":
        "이미 내보내는 중입니다.\n\n"
        "끝나거나 중단한 뒤에 다시 시작하십시오.",
    "Preparing to export…": "내보내기 준비 중…",
    "Exporting {done}/{total}": "내보내는 중 {done}/{total}",
    "Export cancelled": "내보내기 중단",
    "Export finished": "내보내기 완료",
    "{count} copied": "{count}개 복사",
    " · {count} developed": " · 보정 현상 {count}개",
    " · {count} failed": " · 실패 {count}개",
    "\n\nUndo can clear up whatever was written before you stopped.":
        "\n\n중단 시점까지의 작업은 되돌리기로 정리할 수 있습니다.",
    "Nothing to undo": "되돌릴 기록이 없습니다",
    "{name}\n\nUndo this export?": "{name}\n\n이 내보내기를 되돌릴까요?",
    "Undo finished": "되돌리기 완료",
    "{count} cleaned up": "{count}개 정리",

    # ---------------------------------------------------------- preferences
    "Preferences": "설정",
    "Interface language, updates and licences": "인터페이스 언어·업데이트·라이선스",
    "General": "일반",
    "About": "정보",
    "Language": "언어",
    "Interface language": "인터페이스 언어",
    "System default": "시스템 설정",
    "Takes effect the next time the app starts.": "다음 실행부터 적용됩니다.",
    "The interface language changes the next time the app starts.":
        "인터페이스 언어는 다음 실행부터 바뀝니다.",

    "Updates": "업데이트",
    "Check for updates": "업데이트 확인",
    "Off by default. Checking contacts a server and tells it which\n"
    "version is running here. Nothing is sent unless you ask.":
        "기본은 꺼짐입니다. 확인하면 외부 서버에 접속해 이 PC에서 실행 중인\n"
        "버전을 알리게 됩니다. 직접 누르기 전에는 아무것도 나가지 않습니다.",
    "Check now": "지금 확인",
    "Checking…": "확인 중…",
    "No update source is configured for this build.":
        "이 빌드에는 업데이트 확인 주소가 설정되어 있지 않습니다.",
    "Could not reach the update server.": "업데이트 서버에 연결하지 못했습니다.",
    "The update server replied with something unreadable.":
        "업데이트 서버의 응답을 읽지 못했습니다.",
    "Version {latest} is available (this is {current}).":
        "{latest} 버전이 나와 있습니다 (지금은 {current}).",
    "This is the latest version ({current}).": "최신 버전입니다 ({current}).",

    "Non-RAW source: the camera already applied its profile, "
    "colour calibration and lens correction, so those are off.":
        "RAW가 아닙니다. 카메라가 프로파일·기종 색 보정·렌즈 보정을 이미 적용해\n"
        "구워 넣은 결과라 여기서는 꺼 둡니다.",

    "RAW focus selection and develop tool.": "RAW 초점 셀렉트 및 보정 도구.",
    "(file not found: {path})": "(파일을 찾지 못했습니다: {path})",
    "This project's own code is MIT licensed. Bundled data and the\n"
    "libraries used by the packaged build keep their own terms — "
    "PySide6 in particular is LGPL-3.0.":
        "이 프로젝트의 코드는 MIT 라이선스입니다. 함께 담긴 데이터와 배포본이\n"
        "쓰는 라이브러리는 각자의 조건을 따릅니다 — 특히 PySide6는 LGPL-3.0입니다.",

    # 처리되지 않은 예외 알림 (gui/app.py)
    "Error": "오류",
    "An unhandled error occurred.": "처리되지 않은 오류가 발생했습니다.",
    "\n\nError report: {path}": "\n\n오류 보고서: {path}",
    "\n\nFailed to write the log.": "\n\n로그 기록에 실패했습니다.",

    # --- 자동 병합: develop_panel · loupe (i18n)
    'Absolute value based on the capture colour temperature. Lower it for cooler, raise it for warmer':
        '촬영 색온도 기준 절대값. 낮추면 차갑게, 높이면 따뜻하게',
    'Add an info strip below the image':
        '이미지 아래에 정보 띠 붙이기',
    'Add this shot to the queue with its current develop':
        '이 컷을 현재 보정과 함께 대기열에 쌓는다',
    'Add to queue (Q)':
        '대기열 담기 (Q)',
    'Add watermark':
        '워터마크 넣기',
    'Adjusts only the midtones, leaving whites and blacks alone.\nBetter than exposure for lifting just the face of a backlit subject':
        '흰색·검정은 두고 중간톤만 조정합니다.\n역광 인물의 얼굴만 살릴 때 노출보다 이쪽이 맞습니다',
    'All faces':
        '모든 얼굴',
    'Applies the develop set in this window to every shot in the list.\nCrop and straighten are excluded, since framing differs shot to shot.':
        '이 창에서 맞춘 보정을 목록의 모든 컷에 적용합니다.\n크롭·기울이기는 컷마다 구도가 달라서 제외됩니다.',
    'Apply develop to all':
        '보정을 전체에 적용',
    'Apply to':
        '적용 대상',
    'Artist name':
        '작가 이름',
    'Auto lens profile':
        '렌즈 프로필 자동 적용',
    'Background':
        '배경',
    'Balance':
        '균형',
    'Basic':
        '기본',
    'Black background / white text':
        '검은 배경 / 흰 글씨',
    'Blacks':
        '검정 계열',
    'Blending':
        '혼합',
    'Blinks the highlight pixels with blown tone in red':
        '계조가 날아간 밝은 화소를 빨강으로 점멸 표시합니다',
    'Blinks the shadow pixels with crushed tone in blue':
        '계조가 뭉개진 어두운 화소를 파랑으로 점멸 표시합니다',
    'Blue':
        '파랑',
    'Bottom':
        '아래',
    'Brightness':
        '밝기',
    'Browse':
        '찾기',
    'Brush':
        '브러시',
    'Brush (paint by hand)':
        '브러시 (직접 칠하기)',
    "Brush diameter relative to the image's short edge":
        '이미지 짧은 변 대비 붓 지름',
    'Brush size':
        '브러시 크기',
    'By number':
        '번호 지정',
    'Camera color calibration':
        '기종 색 보정',
    'Cannot open this file: {exc}\n(demosaic: {demosaic_exc})':
        '이 파일을 열 수 없습니다: {exc}\n(디모자이크: {demosaic_exc})',
    'Capture info strip':
        '촬영 정보 띠',
    'Choose one directly when the EXIF lens name is missing or differs from the database name.\nCommon with adapters or third-party lenses.':
        'EXIF 렌즈명이 없거나 데이터베이스 이름과 다를 때 직접 고릅니다.\n어댑터나 서드파티 렌즈를 쓰면 흔히 발생합니다.',
    'Chromatic aberration':
        '색수차',
    'Clarity':
        '명료도',
    'Clear all':
        '전부 지우기',
    'Click on the {label} fringing':
        '{label} 언저리를 클릭하십시오',
    'Click the {label} fringing in the preview to set its hue':
        '{label} 언저리를 미리보기에서 클릭해 색조를 지정합니다',
    'Click to add · drag to move · right-click/double-click to delete':
        '클릭 추가 · 드래그 이동 · 우클릭/더블클릭 삭제',
    'Clipping':
        '클리핑',
    'Close':
        '닫기',
    'Color':
        '색상',
    'Color grading':
        '색 보정',
    'Color mixer':
        '색상 혼합',
    'Color noise radius':
        '색상 노이즈 반경',
    'Color noise reduction':
        '색상 노이즈 감소',
    'Contrast':
        '대비',
    'Copyright notice':
        '저작권 표기',
    'Could not delete.':
        '지우지 못했습니다.',
    'Crop / straighten':
        '자르기 / 기울이기',
    'Curve':
        '곡선',
    'Darks':
        '어두움',
    'Default':
        '기본',
    'Dehaze':
        '디헤이즈',
    'Deleted.':
        '지웠습니다.',
    'Deletes the calibration for {camera}.\nNext time you open a folder from this camera, it will offer to recompute.':
        '{camera} 의 보정값을 지웁니다.\n다음에 이 기종의 폴더를 열면 다시 계산을 권합니다.',
    'Destripe':
        '가로 줄무늬 제거',
    'Detail':
        '세부',
    'Detail preservation':
        '디테일 보존',
    'Detected faces — grey boxes, the main subject in red (A).\nClick a face to make it the main subject and re-grade.':
        '검출된 얼굴 — 회색 사각형, 주 피사체는 빨간색 (A).\n얼굴을 클릭하면 주 피사체를 그 얼굴로 바꾸고 판정을 다시 냅니다.',
    'Develop applied to {count} photos (crop and straighten kept per shot)':
        '{count}장에 보정 적용됨 (크롭·기울이기는 컷별 유지)',
    'Develop — {name}':
        '보정 — {name}',
    'Distortion':
        '왜곡',
    'Drag over the image to paint just the area you want':
        '이미지 위에서 드래그해 원하는 영역만 칠합니다',
    'Drop lensfun XML here to widen the list of recognised gear':
        '여기에 lensfun XML을 넣으면 인식 목록이 넓어집니다',
    'EXIF is usually stripped when you post to social media.\nBurned in as visible text, it survives wherever the photo goes.':
        'EXIF는 SNS에 올리면 대부분 지워집니다.\n화면에 보이는 글자로 박아 두면 어디로 가든 남습니다.',
    'EXIF metadata':
        'EXIF 메타데이터',
    'Effects':
        '효과',
    'Eraser':
        '지우개',
    'Erases what you have painted':
        '칠한 영역을 다시 지웁니다',
    'Export this shot right now':
        '이 컷을 지금 바로 내보낸다',
    'Exposure':
        '노출',
    'Eye contours — to check the eyes are really open (E)':
        '눈 윤곽 — 눈이 실제로 떠 있는지 확인용 (E)',
    'Eyes':
        '눈',
    'Face priority':
        '얼굴 우선',
    'Faces':
        '얼굴',
    'Feather':
        '경계 부드럽게',
    'Fills the screen with the region used for grading (Z).\nYou have to zoom in to tell whether focus really landed on the eyes.':
        '판정에 쓴 영역을 화면 가득 채웁니다 (Z).\n눈이 실제로 맞았는지는 확대해서 봐야 압니다.',
    'Final preview failed: {message}':
        '최종 미리보기 실패: {message}',
    'Flip horizontal':
        '좌우 반전',
    'Flip vertical':
        '상하 반전',
    'Focus':
        '초점',
    'Font':
        '글꼴',
    'Frame {value:.1f}':
        '전체 {value:.1f}',
    'GPS location data is never recorded':
        'GPS 위치 정보는 기록하지 않습니다',
    'Grain':
        '그레인',
    'Grain size':
        '그레인 크기',
    'Green':
        '녹색',
    'Highlights':
        '하이라이트',
    'Highlights blown':
        '하이라이트 날아감',
    'Horizontal offset':
        '가로 미세조정',
    'How large a colour blob to catch. Blobs grow larger at\nhigher ISO. Raising it also bleeds true colour edges':
        '얼마나 큰 색 얼룩까지 볼지. 고감도일수록 얼룩이\n커집니다. 올리면 진짜 색 경계도 함께 번집니다',
    'How much to hold back luminance noise reduction outside\nfaces. At high ISO the grain that bothers you is usually\non skin, and the same strength across the whole frame\nsmears fabric weave and hair as well.\n\nMeasured (A6700 ISO3200, noise reduction 70):\n  0 — skin -39% / background detail -20%\n 85 — skin -33% / background detail -6% (default)\n100 — skin -34% / background detail -2%, twice as fast\n\nIgnored on photos with no face':
        '얼굴 밖에서 휘도 노이즈 감소를 얼마나 뺄지.\n고감도에서 거슬리는 것은 대개 피부의 알갱이인데,\n같은 강도를 화면 전체에 걸면 옷의 짜임과\n머리카락까지 뭉갭니다.\n\n실측(A6700 ISO3200, 노이즈 감소 70):\n  0 — 피부 -39% / 배경 디테일 -20%\n 85 — 피부 -33% / 배경 디테일 -6% (기본)\n100 — 피부 -34% / 배경 디테일 -2%, 두 배 빠름\n\n얼굴이 없는 사진에서는 무시됩니다',
    'Hue':
        '색조',
    'Images (*.png *.jpg *.jpeg)':
        '이미지 (*.png *.jpg *.jpeg)',
    'Include EXIF on export':
        '내보낼 때 EXIF 넣기',
    'Invert region':
        '영역 반전',
    'Lateral chromatic aberration — the colour fringing from slight per-channel magnification differences':
        '배율 색수차 — 채널마다 배율이 미세하게 달라 생기는 색 테두리',
    'Left':
        '왼쪽',
    'Lens override':
        '렌즈 지정',
    'Lens profile folder':
        '렌즈 프로필 폴더',
    'Lights':
        '밝음',
    'Loading…':
        '불러오는 중…',
    'Local adjustments (masks)':
        '국소 보정 (마스크)',
    'Local contrast — the large radius makes it the slowest to render':
        '국소 대비 — 반경이 커서 렌더가 가장 느립니다',
    'Looks up the camera and lens in the lensfun database and corrects them.\nFor lenses not in the DB, use the manual correction below.':
        'lensfun 데이터베이스에서 카메라와 렌즈를 찾아 보정합니다.\nDB에 없는 렌즈는 아래 수동 보정을 씁니다.',
    'Luminance':
        '광도',
    "Luminance (brightness) noise. The strength adapts\nautomatically to the photo's real noise, so the same\nvalue gives a similar result across different ISOs":
        '휘도(밝기) 노이즈. 강도는 사진의 실제 노이즈에\n맞춰 자동으로 조절되므로 ISO가 달라도 같은\n값이 비슷한 정도가 됩니다',
    'Main subject':
        '주 피사체',
    'Main subject — the face chosen by focus scoring (the red box on screen)\nAll faces — applied to every detected face\nBy number — largest face first: 1, 2, 3…':
        '주 피사체 — 초점 판정이 고른 얼굴(화면의 빨간 박스)\n모든 얼굴 — 검출된 얼굴 전부에 적용\n번호 지정 — 큰 얼굴부터 1, 2, 3…',
    'Manage camera color calibration':
        '기종 색 보정 관리',
    'Manual':
        '직접 지정',
    'Manual correction':
        '수동 보정',
    'Margin':
        '여백',
    'Mid-frequency detail':
        '중간 주파수 디테일',
    'Midtones':
        '중간 영역',
    'Multiplies the whole image to brighten it. Raising it blows the highlights first':
        '전체를 곱해 밝힙니다. 올리면 하이라이트부터 날아갑니다',
    'Negative corrects barrel (convex), positive corrects pincushion (concave)':
        '음수는 배럴(볼록), 양수는 핀쿠션(오목) 교정',
    'Next shot (→)':
        '다음 컷 (→)',
    'Next ▶':
        '다음 ▶',
    'No clipped pixels to show':
        '표시할 클리핑 화소 없음',
    'No faces detected':
        '검출된 얼굴 없음',
    'Noise method':
        '노이즈 방식',
    'Noise reduction':
        '노이즈 감소',
    'Nudges left or right from the nine-grid position':
        '9분할 위치에서 좌우로 밀어 줍니다',
    'Numbered from the largest face':
        '큰 얼굴부터 매긴 번호',
    "Off by default. When you send a photo out, you often don't want\nyour gear or the capture time going with it.\nLocation data (GPS) is never written under any circumstances.":
        '기본은 꺼짐. 사진을 밖으로 내보낼 때 촬영 장비나 시각이\n딸려 나가는 것을 원치 않는 경우가 많습니다.\n위치 정보(GPS)는 어떤 경우에도 기록하지 않습니다.',
    'Opacity':
        '불투명도',
    'Optics':
        '광학',
    'Or a PNG image':
        '또는 PNG 이미지',
    'Original':
        '원본',
    'Overall strength of the mask effect':
        '마스크 효과의 전체 세기',
    'Paint':
        '칠하기',
    'Position':
        '위치',
    'Positive brightens the corners':
        '양수는 주변부를 밝게',
    'Positive is magenta, negative is green':
        '양수는 마젠타, 음수는 초록',
    'Press this if you added XML while the app was running':
        '앱을 켠 채로 XML을 넣었을 때 누릅니다',
    'Previous shot (←)':
        '이전 컷 (←)',
    'Purple':
        '보라',
    'RAW demosaic failed — showing the embedded JPEG (colour and tone may not be accurate)':
        'RAW 디모자이크 실패 — 내장 JPEG으로 표시 중 (색·계조가 정확하지 않을 수 있습니다)',
    'RGB':
        '밝기',
    'ROI sharpness {value:.1f}':
        'ROI 선명도 {value:.1f}',
    'Radius':
        '반경',
    'Range':
        '범위',
    'Ratio':
        '비율',
    'Recognised: {cameras} bodies · {lenses} lenses':
        '인식 가능: 바디 {cameras}종 · 렌즈 {lenses}종',
    'Red':
        '빨강',
    'Reference hue — purple {purple}° · green {green}°':
        '기준 색조 — 보라 {purple}° · 녹색 {green}°',
    'Reload lens DB':
        '렌즈 DB 다시 읽기',
    'Reloaded — {cameras} bodies · {lenses} lenses':
        '다시 읽었습니다 — 바디 {cameras}종 · 렌즈 {lenses}종',
    'Remove green fringing':
        '녹색 언저리 제거',
    'Remove purple fringing':
        '보라색 언저리 제거',
    'Removes only colour mottling. It does not touch\nluminance, so there is no loss of detail':
        '색 얼룩만 지웁니다. 휘도를 건드리지 않으므로\n디테일 손실이 없습니다',
    "Removes the horizontal banding that appears when an LED\nwall's PWM flicker beats against the rolling shutter.\n\nMeasured (DSC02751 ISO2500 1/800,\n     DSC03868 ISO3200 1/1000):\n  both frames period 103px — the same across ISO and shutter\n  banding cut 71~78%, horizontal detail 99.6% preserved\n  frames without banding are not detected and left alone\n\nBecause it subtracts the same value from every row,\nhorizontal detail is not damaged in principle":
        'LED월의 PWM 점멸과 롤링셔터가 어긋나 생기는\n가로 밴드를 지웁니다.\n\n실측(DSC02751 ISO2500 1/800,\n     DSC03868 ISO3200 1/1000):\n  두 컷 모두 주기 103px — ISO·셔터가 달라도 같습니다\n  줄무늬 71~78% 감소, 가로 디테일 99.6% 보존\n  줄무늬 없는 컷은 아예 검출되지 않아 손대지 않습니다\n\n행마다 같은 값을 빼는 방식이라 가로 방향\n디테일은 원리적으로 손상되지 않습니다',
    'Rendering…':
        '생성 중…',
    'Reset all':
        '전체 초기화',
    "Reset this channel's curve":
        '이 채널 곡선 초기화',
    'Restores the original where there is fine texture like\nhair or foliage. Flat sky or skin is left unaffected':
        '머리카락·나뭇잎처럼 잔무늬가 있는 곳에 원본을\n되살립니다. 평탄한 하늘·피부에는 영향이 없습니다',
    'Right':
        '오른쪽',
    'Rotate 90° left':
        '왼쪽으로 90도',
    'Rotate 90° right':
        '오른쪽으로 90도',
    'Rotation':
        '회전',
    'Rotation 0°':
        '회전 0°',
    'Rotation {deg}°':
        '회전 {deg}°',
    'Sample colour':
        '색 샘플링',
    'Saturation':
        '채도',
    'Saved in: {path}\n\nChoose an item to delete:':
        '저장 위치: {path}\n\n지울 항목을 고르십시오:',
    'Score {score:.1f}':
        '점수 {score:.1f}',
    'Selecting a radial or linear mask shows handles on the image.\nDrag the centre to move, an edge point to resize, an outer point to rotate.':
        '방사형·선형 마스크를 고르면 이미지 위에 조작점이 나타납니다.\n중심을 끌면 이동, 가장자리 점을 끌면 크기, 바깥 점을 끌면 회전입니다.',
    'Shadow (legibility on light backgrounds)':
        '그림자 (밝은 배경에서 가독성)',
    'Shadows':
        '그림자',
    'Shadows crushed':
        '어두운 영역 뭉개짐',
    'Sharpening':
        '선명 효과',
    'Show region':
        '영역 표시',
    'Show where the curve clips tonal values':
        '곡선이 계조를 잘라내는 구간 표시',
    'Shows the area the selected mask covers in red':
        '선택한 마스크가 덮는 영역을 빨갛게 표시합니다',
    'Shows the image before develop (B)':
        '보정 전 이미지를 보여 줍니다 (B)',
    'Straighten':
        '기울이기',
    'Strength':
        '세기',
    'Strip height':
        '띠 높이',
    'Temperature':
        '색온도',
    'Text (e.g. © 2026 Jane Doe)':
        '텍스트 (예: © 2026 홍길동)',
    'Text for the right side (artist name, etc.)':
        '오른쪽에 넣을 문구 (작가명 등)',
    'Text watermark colour':
        '텍스트 워터마크 색',
    'Texture':
        '텍스처',
    'The face, eye and background presets are detected automatically on this frame':
        '얼굴·눈·배경 프리셋은 이 컷에서 자동 인식합니다',
    'The method used to remove luminance noise.\nThe values in parentheses are measured on real R6 Mark III ISO 6400 files:\nthe detail retained and the 32MP processing time when noise is halved.\n\nStandard: detail 99.4% / 0.95s — default for high ISO\nHigh quality: detail 99.9% / 2.6s — for a single large print\nFast: detail 79.9% / 0.34s — a light touch at low ISO\nLegacy: detail 78.7% — only to reproduce older results exactly':
        '휘도 노이즈를 지우는 방식입니다.\n괄호 안은 R6 Mark III ISO 6400 실파일에서 잰 값으로,\n노이즈를 절반으로 줄였을 때 남은 디테일과 32MP 처리 시간입니다.\n\n표준: 디테일 99.4% / 0.95초 — 고감도 기본값\n고품질: 디테일 99.9% / 2.6초 — 크게 인화할 한 장에\n빠름: 디테일 79.9% / 0.34초 — 저감도에서 살짝만\n기존 방식: 디테일 78.7% — 예전 결과를 그대로 재현할 때만',
    'The region used for grading — green box (F)':
        '판정에 쓴 영역 — 초록 사각형 (F)',
    'The size of the detected region. 100 is the default; 0~200% shrinks or grows it.\nApplies only to face, eye and radial masks.':
        '인식 영역의 크기. 100이 기본이고 0~200%로 줄이거나 키웁니다.\n얼굴·눈·방사형 마스크에만 적용됩니다.',
    'The usual preview develops at half resolution for speed.\nWith this on, it re-develops at full resolution to match the\nscreen whenever you stop adjusting — for checking sharpening,\nnoise, and mask retouching at real quality. Zooming in redraws\nit that much more finely.':
        '평소 미리보기는 속도를 위해 절반 해상도로 현상합니다.\n켜 두면 조작이 멈출 때마다 화면 해상도에 맞춰 풀 해상도로\n다시 현상합니다 — 샤픈·노이즈·마스크 리터치를 실제 화질로\n확인할 때 씁니다. 확대(줌)하면 그만큼 더 정밀하게 다시 그립니다.',
    "There is no saved calibration.\n\nOpening a folder of photos from a camera the library doesn't know offers to compute one.\nSaved in: {path}":
        '저장된 보정이 없습니다.\n\n라이브러리가 모르는 기종의 사진 폴더를 열면 계산을 권합니다.\n저장 위치: {path}',
    'This shot has no analysis data':
        '이 컷은 분석 정보가 없습니다',
    'Tint':
        '색조',
    'Top':
        '위',
    'Touches already-saturated colours less (protects skin tones)':
        '이미 진한 색은 덜 건드린다 (피부색 보호)',
    'Vertical offset':
        '세로 미세조정',
    'Vibrance':
        '생동감',
    "View or delete this PC's calibration values, derived by comparing against the camera's built-in JPEG":
        '카메라 내장 JPEG과 비교해 구한 이 PC의 보정값을 확인·삭제합니다',
    'Vignette midpoint':
        '비네팅 중간점',
    'Vignetting':
        '비네팅',
    'Waiting…':
        '대기 중…',
    'Watermark':
        '워터마크',
    'Watermark colour':
        '워터마크 색상',
    'Watermark image':
        '워터마크 이미지',
    'Wheel to zoom · drag to pan · double-click to reset':
        '휠로 확대/축소 · 드래그로 이동 · 더블클릭으로 원래대로',
    'When on, drag on the preview to set the crop.\nDrag a corner to resize, drag inside to move,\ndouble-click to reset to the whole frame.':
        '켜면 미리보기 위에서 드래그로 크롭 범위를 잡습니다.\n모서리를 끌면 크기 조절, 안쪽을 끌면 이동,\n더블클릭하면 전체로 되돌립니다.',
    'When on, drag over the image to paint an area':
        '켜면 이미지 위에서 드래그해 영역을 칠합니다',
    'White background / black text':
        '흰 배경 / 검은 글씨',
    'Whites':
        '흰색 계열',
    'Zoom to focus':
        '초점 확대',
    'crushed {crushed:.2f}% · blown {blown:.2f}%':
        '뭉개짐 {crushed:.2f}% · 날아감 {blown:.2f}%',
    'green':
        '녹색',
    'no calibration needed':
        '보정 불필요',
    'purple':
        '보라',
    '{camera}  —  {state}  ({samples} frames)':
        '{camera}  —  {state}  ({samples}장)',
    '{count} faces detected':
        '검출된 얼굴 {count}개',
    '{label} channel curve':
        '{label} 채널 곡선',
    '{model}: R {r:.3f} · G {g:.3f} · B {b:.3f} ({samples} frames)':
        '{model}: R {r:.3f} · G {g:.3f} · B {b:.3f} ({samples}장)',
    '{model}: no calibration needed':
        '{model}: 보정 불필요',
    '{model}: no saved colour calibration':
        '{model}: 저장된 색 보정 없음',
    '▲ Highlights':
        '▲ 밝은 영역',
    '▼ Shadows':
        '▼ 어두운 영역',
    '◀ Previous':
        '◀ 이전',
    '✂  Crop directly on the image':
        '✂  이미지에서 직접 자르기',
    '＋ Add mask':
        '＋ 마스크 추가',

    # --- 자동 병합 2차: 나머지 GUI + 모듈 dict
    '  (off)':
        '  (꺼짐)',
    '1080px · square/portrait social':
        '1080px · SNS 정사각/세로',
    '1920px · FHD':
        '1920px · FHD',
    '2048px · web':
        '2048px · 웹 게시용',
    '2560px · QHD':
        '2560px · QHD',
    '3840px · 4K/UHD':
        '3840px · 4K/UHD',
    '6000px · for print':
        '6000px · 인화 대비',
    '<br>{count} developed shots':
        '<br>보정된 컷 {count}장',
    'A calibration is already saved — recomputing overwrites it.\n\n':
        '이미 저장된 보정값이 있습니다 — 새로 계산하면 덮어씁니다.\n\n',
    'A radial mask brightens just where you want; adjust position and size afterward.':
        '원형 마스크로 원하는 곳만 밝힙니다. 위치·크기는 이후 조정.',
    'A radial mask darkens just where you want.':
        '원형 마스크로 원하는 곳만 어둡게.',
    'A top linear mask makes the sky bluer and deeper.':
        '위쪽 선형 마스크로 하늘을 더 파랗고 진하게.',
    'Adds clarity and sharpening to the irises to bring out the gaze.':
        '눈동자에 명료도·샤픈을 더해 시선을 살립니다.',
    'Also export bundled JPG/HIF/XMP':
        '함께 저장된 JPG/HIF/XMP도 내보내기',
    'Also export the original RAW':
        '원본 RAW도 함께 내보내기',
    'Aperture':
        '조리개',
    'Applying edit…':
        '보정 적용 중…',
    'Aqua':
        '아쿠아',
    'Artist':
        '작가',
    'Bluer sky':
        '하늘 파랗게',
    'Blur background (bokeh)':
        '배경 흐리게 (아웃포커스)',
    'Brighten area (radial)':
        '부분 밝게 (원형)',
    'Brighten face':
        '얼굴 밝히기',
    'By long edge':
        '긴 변 기준',
    'Camera':
        '카메라',
    'Camera (make/model)':
        '카메라 (제조사/모델)',
    'Cancel':
        '취소',
    'Capture date':
        '촬영 날짜',
    'Capture time':
        '촬영 시각',
    'Click the center to switch the channel display\nTop-left: shadow-clipping warning · top-right: highlight-clipping warning':
        '가운데를 클릭하면 채널 표시가 바뀝니다\n좌상단: 어두운 영역 클리핑 경고 · 우상단: 밝은 영역 클리핑 경고',
    'Click to add a point · drag to move\nRight-click or double-click to delete\nDouble-click an empty area to reset all':
        '클릭해서 점 추가 · 드래그로 이동\n오른쪽 클릭 또는 더블클릭으로 삭제\n빈 곳에서 더블클릭하면 전체 초기화',
    'Color calibration':
        '색 보정',
    'Companion files (JPG/HIF/XMP) included':
        '짝 파일(JPG/HIF/XMP) 포함',
    'Comparing against the built-in JPEGs… ({done}/{total})':
        '내장 JPEG과 비교하는 중… ({done}/{total})',
    'Compute':
        '계산',
    'Compute color calibration on this PC':
        '이 PC에서 색 보정 계산',
    'Compute the color calibration for <b>{camera}</b> yourself.':
        '<b>{camera}</b> 의 색 보정을 직접 계산합니다.',
    'Computing color calibration':
        '색 보정 계산 중',
    'Copyright':
        '저작권',
    'Could not save the calibration.':
        '보정값을 저장하지 못했습니다.',
    'Creates a _keep / _review / _reject folder for each grade to split them':
        '등급마다 _keep / _review / _reject 폴더를 만들어 나눕니다',
    'Custom':
        '직접 입력',
    'Darken area (radial)':
        '부분 어둡게 (원형)',
    'Darkens and desaturates the background to make the subject stand out.':
        '배경을 어둡게·덜 진하게 눌러 인물을 도드라지게.',
    'Darkens outside a central oval to draw the eye in.':
        '가운데 원형 밖을 어둡게 눌러 시선을 모읍니다.',
    'Date taken':
        '촬영 일시',
    'Developed images':
        '현상 이미지',
    'Drag to choose hue and saturation\nCloser to the center is paler, further out is more saturated\nDouble-click to reset':
        '끌어서 색조와 채도를 고른다\n중심에 가까울수록 옅고, 바깥으로 갈수록 진하다\n더블클릭하면 초기화',
    'Emphasize subject (darken background)':
        '인물 강조 (배경 어둡게)',
    'Even if the library already knows this model, the values measured on this PC will take priority.\n\n':
        '라이브러리가 이 기종을 알고 있어도, 이 PC에서 잰 값을 우선하게 됩니다.\n\n',
    'Export options':
        '내보내기 옵션',
    'Exposure (shutter/aperture/ISO)':
        '노출 (셔터/조리개/ISO)',
    'Fast (bilateral filter)':
        '빠름 (양방향 필터)',
    'Filename':
        '파일명',
    'Files':
        '파일',
    'Focal length':
        '초점거리',
    'Format':
        '형식',
    'Free':
        '자유',
    'Grade (keep/review/reject)':
        '등급 (keep/review/reject)',
    'Grades ':
        '등급 ',
    'Grades to export':
        '내보낼 등급',
    'Groups the {located}/{total} shots that have location info\nby nearby coordinates and splits them into folders.\nShots without location go to the _위치없음 folder.':
        '위치 정보가 있는 컷 {located}/{total}장을 좌표가 가까운 것끼리 묶어 폴더로 나눕니다.\n위치가 없는 컷은 _위치없음 폴더로 갑니다.',
    'High quality (non-local means, slow)':
        '고품질 (비국소 평균, 느림)',
    'Hue {hue}°   Saturation {saturation}':
        '색조 {hue}°   채도 {saturation}',
    'ISO':
        'ISO',
    'JPEG (recommended)':
        'JPEG (권장)',
    'Later':
        '나중에',
    'Left as is, the developed colors may come out different from the picture the camera produced.\n\n':
        '이대로 두면 현상 결과의 색이 카메라가 만든 그림과 다르게 나올 수 있습니다.\n\n',
    'Legacy (reproduces old versions)':
        '기존 방식 (구버전 재현용)',
    'Lens':
        '렌즈',
    'Lifts a face darkened by backlight or shade.':
        '역광·그늘로 어두운 얼굴을 끌어올립니다.',
    'Light & sky':
        '조명·하늘',
    'Magenta':
        '마젠타',
    'Move instead of copy':
        '복사 대신 이동',
    'Neutral':
        '중립',
    'New camera model':
        '처음 보는 기종입니다',
    'No histogram':
        '히스토그램 없음',
    'Not enough usable samples. Try again from a folder that contains more photos of this camera model.':
        '쓸 수 있는 표본이 부족합니다. 이 기종의 사진을 더 담은 폴더에서 다시 시도하십시오.',
    "Only '{grade}' is being exported, so there are no grades to split":
        "'{grade}' 하나만 내보내므로 나눌 등급이 없습니다",
    'Orange':
        '주황',
    'Original RAW excluded':
        '원본 RAW 제외',
    'Original RAW included':
        '원본 RAW 포함',
    'Original filename':
        '원본 파일 이름',
    'Original ratio':
        '원본 비율',
    'Original size':
        '원본 크기',
    'PNG (lossless, large)':
        'PNG (무손실, 용량 큼)',
    'Pattern':
        '규칙',
    'Percentage':
        '비율',
    'Portrait':
        '인물',
    'Press an item to drop it into the pattern field':
        '항목을 누르면 규칙 칸에 들어갑니다',
    'Quality':
        '품질',
    'Removes the yellow cast from teeth and brightens slightly. Only affects shots with the mouth open.':
        '치아의 노란기를 빼고 살짝 밝힙니다. 입을 벌린 컷에만 효과가 있습니다.',
    'Render developed images':
        '보정 적용해서 이미지 만들기',
    'Reset this zone to defaults':
        '이 구역을 기본값으로',
    'Reset to default ({value})':
        '기본값({value})으로 되돌린다',
    'Saved the calibration for {camera}.\n\nChannel gain  R {r:.3f} · G {g:.3f} · B {b:.3f}\n{samples} samples\n\nSaved to: {path}':
        '{camera} 보정값을 저장했습니다.\n\n채널 이득  R {r:.3f} · G {g:.3f} · B {b:.3f}\n표본 {samples}장\n\n저장 위치: {path}',
    'Score':
        '점수',
    'Score {score:.1f} · {grade}':
        '점수 {score:.1f} · {grade}',
    'Sequence number (0001…)':
        '일련번호 (0001…)',
    'Sharpen irises':
        '눈동자 또렷하게',
    'Shutter':
        '셔터',
    'Skin smoothing':
        '부드럽게(피부)',
    'Smooth skin':
        '피부 매끄럽게',
    'Smooths skin across the whole face; texture eased slightly.':
        '얼굴 전체 피부를 부드럽게. 질감은 살짝 낮춥니다.',
    'Softens under-eye lines and dark circles, and lifts brightness a touch.':
        '눈밑 주름·다크서클을 은은하게 펴고 아주 살짝 밝힙니다.',
    'Softly blurs only the background for a shallow depth-of-field look.':
        '배경만 부드럽게 흐려 얕은 심도 느낌을 냅니다.',
    'Software':
        '소프트웨어',
    'Split into folders by grade (_keep / _review / _reject)':
        '등급별 폴더로 나누기 (_keep / _review / _reject)',
    'Split into folders by location (GPS)':
        '장소별 폴더로 나누기 (GPS 기준)',
    'Spotlight (darken surroundings)':
        '스포트라이트 (주변 어둡게)',
    'Standard (non-local means)':
        '표준 (비국소 평균)',
    'TIFF (lossless, for print/re-edit)':
        'TIFF (무손실, 인쇄·재보정용)',
    "The calibration is computed by comparing {count} photos in this folder against the camera's built-in JPEGs.\n\n· It takes anywhere from a few seconds to tens of seconds\n· The result is saved only to this PC's data folder\n· You can delete or recompute it later in the Optics section\n\nCompute now?":
        '이 폴더의 사진 {count}장을 카메라 내장 JPEG과 비교해 보정값을 계산합니다.\n\n· 몇 초에서 수십 초 걸립니다\n· 결과는 이 PC의 data 폴더에만 저장됩니다\n· 나중에 광학 섹션에서 지우거나 다시 계산할 수 있습니다\n\n지금 계산하시겠습니까?',
    'The library does not yet know the color of <b>{camera}</b>.':
        '<b>{camera}</b> 의 색 정보를 라이브러리가 아직 모릅니다.',
    'The originals disappear from their original location. Recoverable with undo.':
        '원본이 제자리에서 사라집니다. 되돌리기로 복구 가능.',
    'This batch has no RAW (JPEG·HIF only). With no originals to keep and no companion files, the two options above cannot be used — developed shots are exported as their rendered image, and shots that were not developed are exported as-is.':
        '이 배치에는 RAW가 없습니다 (JPEG·HIF만). 남길 원본도 짝 파일도 없어서 위 두 항목은 쓸 수 없습니다 — 보정한 컷은 현상본이, 보정하지 않은 컷은 원본 그대로가 나갑니다.',
    'This batch has no shots with location info.\nIf the camera body has no GPS, you have to shoot linked to a\nphone for it to be recorded.\nTurn it on now and everything goes into the _위치없음 folder.':
        '이 배치에는 위치 정보가 있는 컷이 없습니다.\n바디에 GPS가 없으면 폰과 연동해 찍어야 기록됩니다.\n지금 켜면 전부 _위치없음 폴더로 들어갑니다.',
    "Toggle this section's edits on and off (values kept)":
        '이 섹션의 보정을 껐다 켠다 (값은 유지)',
    'Under-eye retouch':
        '언더아이 리터치',
    'When off, only the developed images are exported. Copying the originals too doubles the size.':
        '끄면 현상된 이미지만 나갑니다. 원본까지 복사하면 용량이 두 배가 됩니다.',
    'When shot as RAW+JPEG or RAW+HEIF, the same-named companion file\nis moved along with it.\nWhen off, only the RAW is exported.':
        'RAW+JPEG 또는 RAW+HEIF로 찍었을 때 같은 이름의 짝 파일을 함께 옮깁니다.\n끄면 RAW만 나갑니다.',
    'Whiten teeth':
        '치아 화이트닝',
    'Yellow':
        '노랑',
    'collected in one folder':
        '한 폴더에 모음',
    'developed shots rendered as {fmt}':
        '보정된 컷은 {fmt} 로 현상',
    'e.g. {example}{suffix}':
        '예: {example}{suffix}',
    'long edge {px}px':
        '긴 변 {px}px',
    'move (originals disappear)':
        '이동(원본 사라짐)',
    'none selected → all':
        '선택 없음 → 전체',
    '{camera} did not need any calibration.\nIts difference from the camera JPEGs is already small enough.':
        '{camera} 는 보정이 필요하지 않았습니다.\n카메라 JPEG과의 차이가 이미 충분히 작습니다.',
    '{description} — press to insert into the pattern':
        '{description} — 누르면 규칙에 넣습니다',
    '{pct}% size':
        '{pct}% 크기',
    '· Center':
        '· 정가운데',
    '← Middle-left':
        '← 좌측 가운데',
    '↑ Top-center':
        '↑ 상단 가운데',
    '→ Middle-right':
        '→ 우측 가운데',
    '↓ Bottom-center':
        '↓ 하단 가운데',
    '↖ Top-left':
        '↖ 좌상단',
    '↗ Top-right':
        '↗ 우상단',
    '↘ Bottom-right':
        '↘ 우하단',
    '↙ Bottom-left':
        '↙ 좌하단',
}
