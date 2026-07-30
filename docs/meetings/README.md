# [soma0sd_RPi_Schedule Meetings & Minutes / 회의 및 회의록 관리]

본 디렉토리는 해당 프로젝트의 회의록 및 관련 문서를 아카이빙하는 공간입니다.

## 1. 회의록 관리 규약 (Standard Operating Procedure)

1. **노션(Notion) DB 저장 (Primary)**:
   - 모든 회의록은 **노션 '하늘소프트 작업 일지' DB** (업무 종류: 회의록)에 우선 저장합니다.
   - **노션 페이지 제목 양식**: [soma0sd_RPi_Schedule] YYYY-MM-DD 회의록: [주요 의제]`n     - 예: [soma0sd_RPi_Schedule] 2026-07-24 회의록: 주요 안건 논의`n
2. **로컬 Git Repository 저장 (Secondary / Archiving)**:
   - 본 디렉토리(docs/meetings/)에 마크다운 파일로 회의록을 추가 보관합니다.
   - **로컬 파일명 양식**: YYYY-MM-DD_[soma0sd_RPi_Schedule]_[주요의제].md`n     - 예: 2026-07-24_soma0sd_RPi_Schedule_주요안건논의.md`n
3. **공통 가이드라인 & 표준 템플릿**:
   - 가이드라인: [MEETING_MANAGEMENT_GUIDE.md](file:///d:/Project/MEETING_MANAGEMENT_GUIDE.md)
   - 표준 템플릿: [MEETING_TEMPLATE.md](file:///d:/Project/MEETING_TEMPLATE.md)

---

## 2. 표준 회의록 마크다운 양식 (Quick Template)

`markdown
# [soma0sd_RPi_Schedule] YYYY-MM-DD 회의록: [주요 의제/제목]

## 1. 회의 개요
- **일시**: YYYY-MM-DD HH:MM ~ HH:MM
- **장소**: [대회의실 / Zoom / Google Meet / 현장 등]
- **참석자**: [소속/이름]
- **작성자**: [소속/이름]

## 2. 회의 목적 및 안건
1. 안건 1: 
2. 안건 2: 

## 3. 핵심 요약 (Executive Summary)
- [3~5줄 내외의 핵심 결과 요약]

## 4. 세부 논의 내용
### 4.1 안건 1
- 논의 사항 및 주요 의견

## 5. 의결 및 결정 사항 (Decisions)
- [x] **[결정사항 1]**: 

## 6. Action Items (조치 사항 및 향후 일정)
- [ ] **[담당자]** [작업 내용 1] (기한: YYYY-MM-DD)
``n
