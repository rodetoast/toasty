# AsepriteTagMaster 개발 현황

확인일 2026-11-24 · 기본 브랜치 `main` · [개발 브랜치](https://github.com/rodetoast/toasty/tree/codex/migration-checkpoint-20261117) · [조사 기준 소스 커밋](https://github.com/rodetoast/toasty/commit/08d15a900fc05f4c8fe45539114c15435b1c4ed1)

애니메이터가 Aseprite 태그 이름과 속성을 규칙에 맞춰 일괄 검수·수정합니다.

- 제품 표시명 **AsepriteTagMaster** · 런처 app_id `tag-master`
- 기술 구성 Python, PyQt6, Aseprite Lua, PyInstaller
- 구현 기능 파일 일괄 불러오기, 규칙·사전 기반 검수, 변경 미리보기와 개별 적용 선택

## 배포 상태

**0.4.0** · `0.4.0-team-7263f8d` · `tag-master--0.4.0-team-7263f8d.zip` (onefile) · 실행 파일 `AsepriteTagMaster.exe`. [Rod 운영 카탈로그](https://github.com/rodetoast/creative-group-releases/releases/tag/rodetoast-catalog-000015)

소스 저장소의 독립 Latest Release는 확인되지 않았습니다.

## 개발과 검증

소스와 운영 버전 모두 0.4.0입니다. 후속 바이너리는 미확인입니다.

**알려진 제약과 미검증** 파일 적용 시 Aseprite 경로 설정이 필요합니다. 호스트와 실제 저장은 재시험하지 않았습니다.

이번 확인에서는 원격 Git과 공개 서명 카탈로그를 대조했습니다. 기능 시험과 패키징은 기존 보고서의 범위를 따릅니다.

## 패키징과 다음 단계

README의 main.py 실행, create_build_info.py 및 AsepriteTagMaster.spec 빌드 절차를 따릅니다. [공통 배포 절차](https://github.com/rodetoast/southpaw-development-hub/blob/main/docs/RELEASE_GUIDE.md)

**다음 단계** 새 규칙 변경 요청 시 미리보기와 합성 파일 저장을 먼저 검증합니다. 신규 버전은 미정입니다.

[README.md](https://github.com/rodetoast/toasty/blob/08d15a900fc05f4c8fe45539114c15435b1c4ed1/README.md) · [AsepriteTagMaster.spec](https://github.com/rodetoast/toasty/blob/08d15a900fc05f4c8fe45539114c15435b1c4ed1/AsepriteTagMaster.spec) · [개발 허브 요약](https://github.com/rodetoast/southpaw-development-hub/blob/main/projects/tag-master.md)
