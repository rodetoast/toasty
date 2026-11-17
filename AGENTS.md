## 지속적인 개발·기획 기록 (2026-11-17)

프로젝트: **AsepriteTagMaster** — Aseprite 태그 검수·일괄 수정.

새 세션은 이 지침과 아래 문서를 읽고 `git status`, branch/HEAD/remote를 확인한다. 문서에 근거 날짜·커밋·실제 배포 버전을 구분하고, 충돌하는 과거 기록을 현재 상태로 간주하지 않는다.

### 기록 위치

- [README.md](README.md): 목적·기능·사용법.
- [DEV_LOG.md](DEV_LOG.md): 개발 상태·결정과 이유·검증·배포 이력·다음 작업.
- [STABILITY_PLAN.md](STABILITY_PLAN.md): 미완료 안정화·검증 계획.

### 작업과 Git 기록 규칙

- 중요한 기획·설계 결정은 실제 전달받은 내용, 이유·대안/영향과 함께 위 개발 기록에 남긴다. 접근하지 못한 대화의 내용을 만들지 않는다.
- 의미 있는 작업 완료 시 관련 코드와 문서를 함께 커밋한다. 테스트 명령·결과·환경·미검증 범위, 완료/미완료와 다음 작업을 구분한다. 대화 메시지마다 커밋하지 않는다.
- Release 후 위 현행 상태 문서와 기존 CHANGELOG/릴리스 기록에 버전·태그/URL·검증 결과를 갱신한다. CHANGELOG가 없으면 지정한 개발 기록에 누적하고 같은 내용을 여러 문서에 복제하지 않는다.
- 안전하고 승인된 개발 브랜치에서 공개 범위·계정·remote·충돌·민감정보를 확인하고 정상 push한다. 거절/불일치가 나면 강제 push·reset·clean·기존 변경 덮어쓰기로 해결하지 않는다.
- 기존 미커밋 작업을 한꺼번에 포함하지 말고 관련 파일/hunk를 명시한다. 분리가 어려우면 원본을 보존한 격리 체크포인트를 사용하고 제외/미반영 범위를 기록한다.
- 개인키·토큰·개인/직원 자료·실행 데이터·무분별한 빌드 산출물을 소스 커밋에 넣지 않는다. 로컬 커밋, 원격 push, 검증 통과, 정식 Release 공개는 각각 별도 상태다.
- 소스 push는 제품 운영 Release·카탈로그·Latest 변경 승인이 아니다. 계정 이전 호환성이 확인되기 전 toast-source 고정 주소·신뢰키·guard를 바꾸거나 배포 저장소를 Transfer하지 않는다.


## 실제 소스 이전 및 개발 재개 (2026-11-17)

- 실제 신규 저장소: https://github.com/rodetoast/toasty (Public 유지). 이전 소스 주소: https://github.com/toast-source/toasty. 실제 ID/owner·이력·기존 자산 보존을 확인한 후 기록했다.
- 개발 PC 원본 작업 경로: `C:/Users/SOUTHPAW GAMES/Desktop/AsepriteTagMaster_Source`. 원본 branch `main`, HEAD `7263f8d6107be76363d329c6962cd46dd15d4068`와 기존 미커밋 작업을 유지한다. 소스 보존을 위해 원본을 강제 checkout/reset/병합하지 않는다.
- 최신 개발 소스·기획 보존 브랜치: `codex/migration-checkpoint-20261117`. 기존 선별 소스/문서 기준 commit은 `3c0eff8516a9a6b5ba5fce4fd09d74e6d4559759`이며 이후 주소·인계 문서 변경이 이어진다. 최신 실제 commit은 해당 원격 branch와 `git log -1`로 확인한다.
- 기본 main은 기존 코드·이력을 유지하는 문서 진입점이다. 최신 개발을 이어가려면 위 branch의 AGENTS와 연결 문서를 읽고 Git 상태부터 확인한다. dirty 원본에서 무조건 branch를 전환하지 않는다. 승인된 소스 push 대상은 rodetoast 저장소의 검토된 개발 branch다.
- 원본의 미커밋 변경은 그대로 남긴다. 기존 체크포인트에 선별한 코드/기획 문서를 보존했으며 개인/업무 작업 파일·실행 설정·임의 개발 산출물은 새로 일괄 업로드하지 않았다. 현재 원본 `git status`와 제외 범위를 확인하고 미반영 작업을 폐기하지 않는다.
- 기존 README·설계 조사·개발 로그·작업 큐 및 미완료 QA를 유지한다. 소스 보존 커밋·원격 push는 정식 버전 배포, 기능 승인, 새로운 회귀 통과가 아니다. 이번 Git 이전에서는 제품 코드/이름/아이콘을 새로 변경하거나 EXE를 재빌드/실행하지 않았다.
- 운영/시험 배포 및 private 증거 저장소는 아직 toast-source다. 다운로드/업데이트 URL·서명·카탈로그·인증 owner guard·app_id/Windows AppUserModelID를 소스 주소와 함께 일괄 치환하지 않는다.
