"""4단계 검증용: 가상의 IT 서비스 회사 분개장과 정답표를 만듭니다.

AuditLens 애플리케이션 코드(auditlens/, app.py)는 쓰지도 바꾸지도 않습니다. 이 스크립트는 파일만 만듭니다.

- 회사: 가상클라우드솔루션(주) (가상). 거래처·입력자도 모두 가상입니다. 실제 회사·개인과 관계없습니다.
- 기간: 2024-01-01 ~ 2025-12-31 (12월 결산, 2개 회계연도)
- 전표번호: "ITS-연도-번호" (IT Service, 가상)
- 난수 시드를 고정해 몇 번을 실행해도 같은 파일이 나옵니다.
- 부가가치세 분개는 생략했습니다(단순화).

출력
- upload/가상IT_분개장_2024_2025.csv            차변·대변 두 열 (UTF-8, BOM)
- upload/가상IT_분개장_2024_2025.xlsx           같은 데이터, Excel
- upload/가상IT_분개장_단일금액_2024_2025.csv   같은 데이터, 금액 한 열 + 차대구분('차'/'대')
- ground_truth/가상IT_정답표.csv                 검증용 정답표 (프로그램에 올리지 않음)

실행: python validation/it_service/generate_it_service_data.py
"""

import random
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

SEED = 20251231
HERE = Path(__file__).resolve().parent
UPLOAD = HERE / "upload"
TRUTH = HERE / "ground_truth"

COMPANY = "가상클라우드솔루션(주)"
ACCOUNTS = {
    "101": "현금", "103": "보통예금", "120": "미수금", "253": "미지급금", "254": "예수금",
    "404": "용역매출", "801": "급여", "819": "임차료", "831": "지급수수료", "833": "광고선전비",
    "835": "외주용역비", "838": "소프트웨어사용료",
}

# (거래처, 월 청구액 기준, 청구일)
CUSTOMERS = [("가상핀테크(주)", 12_000_000, 10), ("가상물류(주)", 8_000_000, 10),
             ("가상헬스케어(주)", 15_000_000, 11), ("가상리테일(주)", 6_000_000, 11),
             ("가상에듀(주)", 4_000_000, 12), ("가상미디어(주)", 10_000_000, 12),
             ("가상모빌리티(주)", 20_000_000, 13), ("가상공공서비스(주)", 7_000_000, 13)]
# (거래처, 월 외주비 기준, 계상일, 용역 내용)
SUBCONTRACTORS = [("가상개발랩(주)", 9_000_000, 14, "앱 개발"), ("가상디자인스튜디오", 6_000_000, 14, "UI 디자인"),
                  ("가상QA파트너스(주)", 5_000_000, 15, "품질 테스트"), ("가상인프라웍스(주)", 12_000_000, 15, "서버 운영"),
                  ("가상데이터웍스(주)", 8_000_000, 16, "데이터 분석"), ("가상보안컨설팅(주)", 7_000_000, 16, "보안 점검")]
# (거래처, 월 사용료 기준, 변동 폭, 계상일)  변동 폭 0 = 정액 구독
SOFTWARE = [("가상클라우드", 3_200_000, 0.2, 8), ("가상협업툴", 900_000, 0.0, 9), ("가상디자인툴", 600_000, 0.0, 9),
            ("가상코드저장소", 450_000, 0.0, 10), ("가상보안SW", 1_500_000, 0.0, 10)]
# (거래처, 월 집행액 기준, 계상일)
ADS = [("가상검색광고", 2_500_000, 15), ("가상SNS광고", 1_800_000, 16), ("가상동영상광고", 1_200_000, 17)]
LANDLORD, RENT = "가상빌딩관리(주)", 5_500_000
PAYROLL = [("개발팀", 52_000_000), ("영업팀", 18_000_000), ("관리팀", 14_000_000)]

MONTHS = [(y, m) for y in (2024, 2025) for m in range(1, 13)]
BURST_DAYS = [6, 7, 10, 11, 12, 13, 14, 17, 18, 19, 20, 21]  # 2025-11 광고 집중 집행일(모두 평일)


def weekday_on_or_before(y, m, d):
    day = date(y, m, d)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def next_month(y, m):
    return (y + 1, 1) if m == 12 else (y, m + 1)


class Book:
    def __init__(self, rng):
        self.rng = rng
        self.vouchers = []

    def add(self, posting, lines, desc, vendor="", source="자동", je_type="일반", creator="SYSTEM",
            approver="mgr01", entry=None, case=None):
        """lines: [(계정코드, 차변, 대변) 또는 (계정코드, 차변, 대변, 라인 적요)]"""
        dr = sum(l[1] for l in lines)
        cr = sum(l[2] for l in lines)
        assert dr == cr, (desc, dr, cr)
        if entry is None:
            if source == "자동":
                entry = datetime.combine(posting, datetime.min.time()) + timedelta(
                    hours=self.rng.randint(9, 18), minutes=self.rng.randint(0, 59))
            else:  # 수기: 같은 날 또는 다음 영업일 업무 시간
                d = posting + timedelta(days=self.rng.randint(0, 1))
                while d.weekday() >= 5:
                    d += timedelta(days=1)
                entry = datetime.combine(d, datetime.min.time()) + timedelta(
                    hours=self.rng.randint(9, 17), minutes=self.rng.randint(0, 59))
        self.vouchers.append(dict(posting=posting, entry=entry, lines=lines, desc=desc, vendor=vendor,
                                  source=source, je_type=je_type, creator=creator, approver=approver,
                                  case=case, seq=len(self.vouchers)))

    def amount(self, base, pct):
        return int(round(base * self.rng.uniform(1 - pct, 1 + pct), -3))


def build():
    rng = random.Random(SEED)
    b = Book(rng)

    # ---------------- 파일 시작 전(2023년 12월) 발생분: 2024년 1월에 회수·지급만 기록
    for i, (name, base, _) in enumerate(CUSTOMERS):
        amt = b.amount(base, 0.15)
        b.add(weekday_on_or_before(2024, 1, 18 + i % 4), [("103", amt, 0), ("120", 0, amt)],
              "2023년 12월 용역 대금 입금(전기 이월 채권)", vendor=name)
    for name, base, _, _ in SUBCONTRACTORS:
        amt = b.amount(base, 0.15)
        b.add(weekday_on_or_before(2024, 1, 10), [("253", amt, 0), ("103", 0, amt)],
              "2023년 12월 외주 용역비 지급(전기 이월 채무)", vendor=name)
    for name, base, pct, _ in SOFTWARE:
        amt = b.amount(base, pct)
        b.add(weekday_on_or_before(2024, 1, 20), [("253", amt, 0), ("103", 0, amt)],
              "2023년 12월 소프트웨어 사용료 지급(전기 이월 채무)", vendor=name)
    for name, base, _ in ADS:
        amt = b.amount(base, 0.2)
        b.add(weekday_on_or_before(2024, 1, 22), [("253", amt, 0), ("103", 0, amt)],
              "2023년 12월 광고비 지급(전기 이월 채무)", vendor=name)
    wh = int(round(sum(base for _, base in PAYROLL) * 0.1, -3))
    b.add(weekday_on_or_before(2024, 1, 10), [("254", wh, 0), ("103", 0, wh)], "2023년 12월분 원천세·4대보험 납부")

    for y, m in MONTHS:
        ny, nm = next_month(y, m)
        in_file_next = (ny, nm) <= (2025, 12)

        # ---------------- 용역매출 청구와 다음 달 회수
        for i, (name, base, day) in enumerate(CUSTOMERS):
            amt, case = b.amount(base, 0.15), None
            if (name, y, m) == ("가상공공서비스(주)", 2025, 9):
                amt, case = 45_000_000, "C-R2A-01"
            if (name, y, m) == ("가상핀테크(주)", 2025, 8):
                amt, case = 16_800_000, "N-R2A-01"
            b.add(weekday_on_or_before(y, m, day), [("120", amt, 0), ("404", 0, amt)],
                  f"{y}년 {m}월 시스템 운영·유지보수 용역 대금 청구", vendor=name, case=case)
            if in_file_next:
                b.add(weekday_on_or_before(ny, nm, 18 + i % 4), [("103", amt, 0), ("120", 0, amt)],
                      f"{y}년 {m}월 용역 대금 입금", vendor=name, case=case)

        # ---------------- 외주용역비 계상(전자세금계산서 연동)과 다음 달 지급
        for name, base, day, work in SUBCONTRACTORS:
            amt = b.amount(base, 0.15)
            posting, entry, case = weekday_on_or_before(y, m, day), None, None
            if (name, y, m) == ("가상QA파트너스(주)", 2024, 12):
                posting, entry, case = date(2024, 12, 16), datetime(2025, 1, 14, 10, 20), "C-R3B-01"
            if (name, y, m) == ("가상QA파트너스(주)", 2025, 12):
                posting, entry, case = date(2025, 12, 15), datetime(2025, 12, 31, 17, 30), "N-R3B-01"
            if (name, y, m) == ("가상인프라웍스(주)", 2025, 10):
                amt, case = 18_000_000, "N-R1-01"
            b.add(posting, [("835", amt, 0), ("253", 0, amt)], f"{m}월 {work} 외주 용역비", vendor=name,
                  entry=entry, case=case)
            if in_file_next:
                kw = {}
                if (name, ny, nm) == ("가상디자인스튜디오", 2025, 3):
                    kw = dict(creator="acct02", approver="acct02", case="C-R4B-01")
                b.add(weekday_on_or_before(ny, nm, 11), [("253", amt, 0), ("103", 0, amt)],
                      f"{m}월 외주 용역비 지급", vendor=name, **kw)

        # ---------------- 소프트웨어 사용료(클라우드·구독) 계상과 다음 달 지급
        for name, base, pct, day in SOFTWARE:
            amt = b.amount(base, pct)
            posting, kw = weekday_on_or_before(y, m, day), {}
            if (y, m) == (2025, 8) and name in ("가상협업툴", "가상디자인툴"):
                kw = dict(creator="acct09", case="C-R4C-01")
            if (name, y, m) == ("가상코드저장소", 2025, 10):
                posting, kw = date(2025, 10, 8), dict(case="C-R6-01")
            b.add(posting, [("838", amt, 0), ("253", 0, amt)], f"{m}월 {name} 사용료", vendor=name, **kw)
            if (name, y, m) == ("가상코드저장소", 2025, 10):  # 같은 청구서를 이틀 뒤 한 번 더 입력
                b.add(date(2025, 10, 10), [("838", amt, 0), ("253", 0, amt)], f"{m}월 {name} 사용료",
                      vendor=name, case="C-R6-01")
            if in_file_next:
                b.add(weekday_on_or_before(ny, nm, 20), [("253", amt, 0), ("103", 0, amt)],
                      f"{m}월 {name} 사용료 지급", vendor=name)

        # ---------------- 광고선전비 집행과 다음 달 지급(거래처별 월 합계)
        for name, base, day in ADS:
            if (y, m) == (2025, 11):
                k = [a for a, _, _ in ADS].index(name)
                runs = [(date(2025, 11, d), int(round(base * rng.uniform(0.8, 1.0), -3))) for d in BURST_DAYS[k::3]]
                for posting, amt in runs:
                    b.add(posting, [("833", amt, 0), ("253", 0, amt)], f"11월 연말 프로모션 {name} 집행비",
                          vendor=name, case="C-R2B-01")
                total = sum(a for _, a in runs)
            else:
                amt = b.amount(base, 0.2)
                posting, entry, kw = weekday_on_or_before(y, m, day), None, {}
                if name == "가상동영상광고" and y == 2025:
                    kw = dict(creator="acct03", case="N-R4C-01")
                if (y, m) == (2025, 6) and name == "가상SNS광고":
                    posting, entry, kw = date(2025, 6, 13), datetime(2025, 6, 14, 11, 0), dict(case="C-R3C-01")
                if (y, m) == (2025, 6) and name == "가상검색광고":
                    posting, entry, kw = date(2025, 6, 13), datetime(2025, 6, 13, 23, 50), dict(case="N-R3C-01")
                b.add(posting, [("833", amt, 0), ("253", 0, amt)], f"{m}월 {name} 집행비", vendor=name,
                      entry=entry, **kw)
                total = amt
            if in_file_next:
                b.add(weekday_on_or_before(ny, nm, 22), [("253", total, 0), ("103", 0, total)],
                      f"{m}월 {name} 광고비 지급", vendor=name)

        # ---------------- 임차료(자동이체)
        b.add(weekday_on_or_before(y, m, 10), [("819", RENT, 0), ("103", 0, RENT)], f"{m}월 사무실 임차료",
              vendor=LANDLORD, case="N-R6-02" if (y, m) in [(2025, 2), (2025, 3)] else None)

        # ---------------- 급여(인사 시스템 연동)와 다음 달 원천세 납부
        pay = [(team, b.amount(base, 0.03)) for team, base in PAYROLL]
        gross = sum(a for _, a in pay)
        wh = int(round(gross * 0.1, -3))
        lines = [("801", a, 0, f"{m}월 급여 - {team}") for team, a in pay]
        lines += [("254", 0, wh, f"{m}월 급여 원천징수·4대보험 예수"), ("103", 0, gross - wh, f"{m}월 급여 지급")]
        b.add(weekday_on_or_before(y, m, 24), lines, f"{m}월 급여", case="N-R3A-01" if (y, m) == (2025, 12) else None)
        if in_file_next:
            b.add(weekday_on_or_before(ny, nm, 10), [("254", wh, 0), ("103", 0, wh)], f"{m}월분 원천세·4대보험 납부")

        # ---------------- 지급수수료와 소액현금
        drop = (y, m) == (2025, 7)  # 2025년 7월: 지급수수료 전표 대부분이 빠진 달(입력 누락 가정)
        if not drop:
            fee = int(round(rng.uniform(30_000, 60_000), -2))
            b.add(weekday_on_or_before(y, m, 20), [("831", fee, 0), ("103", 0, fee)], f"{m}월 이체 수수료",
                  vendor="가상은행")
            b.add(weekday_on_or_before(y, m, 15), [("831", 1_100_000, 0), ("103", 0, 1_100_000)],
                  f"{m}월 기장·세무 자문 수수료", vendor="가상세무회계", source="수기", creator="acct01", approver="mgr02")
            b.add(weekday_on_or_before(y, m, 20), [("831", 330_000, 0), ("103", 0, 330_000)],
                  f"{m}월 급여 계산 대행 수수료", vendor="가상급여대행")
            blank = (y, m) == (2025, 10)
            b.add(weekday_on_or_before(y, m, 18), [("831", 220_000, 0), ("103", 0, 220_000)],
                  "" if blank else f"{m}월 보안 인증 유지 수수료", vendor="가상인증서비스",
                  case="C-R4D-01" if blank else None)
            park = int(round(rng.uniform(5_000, 20_000), -3))
            b.add(weekday_on_or_before(y, m, 19), [("831", park, 0), ("101", 0, park)], "외근 주차비", vendor="가상주차장",
                  source="수기", creator="acct02", approver="mgr02")
        quick = int(round(rng.uniform(15_000, 60_000), -3))
        b.add(weekday_on_or_before(y, m, 12), [("831", quick, 0), ("101", 0, quick)], "퀵 배송비", vendor="가상퀵서비스",
              source="수기", creator="acct02", approver="mgr02", case="C-R2B-02" if drop else None)
        b.add(weekday_on_or_before(y, m, 9), [("101", 200_000, 0), ("103", 0, 200_000)], "소액현금 보충",
              source="수기", creator="acct02", approver="mgr02")

    # ================= 규칙별 검증 사례(위 반복 거래에 넣지 않은 것)
    # R1: 처음 거래하는 외주처의 일회성 대형 외주비
    b.add(date(2025, 4, 15), [("835", 60_000_000, 0), ("253", 0, 60_000_000)],
          "AI 상담 모델 구축 외주 용역 (일회성 프로젝트)", vendor="가상AI컨설팅(주)", case="C-R1-01")
    b.add(date(2025, 5, 12), [("253", 60_000_000, 0), ("103", 0, 60_000_000)],
          "AI 상담 모델 구축 외주 용역비 지급", vendor="가상AI컨설팅(주)", case="N-R1-02")
    # R3(a): 결산일 3영업일 전 추가 청구
    b.add(date(2025, 12, 26), [("120", 15_000_000, 0), ("404", 0, 15_000_000)], "연말 추가 개발 용역 대금 청구",
          vendor="가상헬스케어(주)", entry=datetime(2025, 12, 26, 15, 0), case="C-R3A-01")
    # 정상 결산 분개(연차수당 미지급 계상): R3(a)와 R4(a)에 걸릴 것으로 예상되는 정상 거래
    for y, amt in [(2024, 3_500_000), (2025, 3_800_000)]:
        b.add(date(y, 12, 31), [("801", amt, 0), ("253", 0, amt)], "연말 미사용 연차수당 미지급 계상",
              source="수기", je_type="결산", creator="acct01", approver="mgr02",
              entry=datetime(y, 12, 31, 18, 0), case="FP-R3-01")
    # 여러 규칙 동시: 결산일 매출 조정, 결산 후 토요일 입력, 입력자=승인자, 적요 '조정'
    b.add(date(2025, 12, 31), [("120", 9_000_000, 0), ("404", 0, 9_000_000)], "12월 용역매출 조정 계상",
          vendor="가상리테일(주)", source="수기", je_type="조정", creator="acct01", approver="acct01",
          entry=datetime(2026, 1, 3, 14, 0), case="M-01")
    # R4(a)만: 계정 재분류 조정 전표
    b.add(date(2025, 5, 20), [("831", 300_000, 0), ("833", 0, 300_000)], "SNS 광고 대행 수수료 계정 재분류",
          vendor="가상SNS광고", source="수기", je_type="조정", creator="acct02", approver="mgr02", case="C-R4A-01")
    # R4(d) 키워드 오탐: 정상 적요에 '조정'이 들어 있음
    b.add(date(2025, 4, 17), [("831", 120_000, 0), ("101", 0, 120_000)], "분기 일정 조정 회의 회의실 대관료",
          vendor="가상공유오피스", source="수기", creator="acct02", approver="mgr02", case="FP-R4D-01")
    # R4(d) 키워드: '임시'
    b.add(date(2025, 9, 22), [("838", 800_000, 0), ("253", 0, 800_000)], "9월 클라우드 추가 사용료 임시 청구분",
          vendor="가상클라우드", case="C-R4D-02")
    # R5: 마지막 2개월 신규 거래처(기준금액 이상), 기존 거래처의 신규 계정, 기준금액 미만 신규 거래처
    b.add(date(2025, 11, 18), [("835", 21_000_000, 0), ("253", 0, 21_000_000)], "데이터 파이프라인 구축 외주 용역",
          vendor="가상데이터랩(주)", case="C-R5-01")
    b.add(date(2025, 12, 15), [("253", 21_000_000, 0), ("103", 0, 21_000_000)], "데이터 파이프라인 구축 외주 용역비 지급",
          vendor="가상데이터랩(주)", case="C-R5-01")
    b.add(date(2025, 12, 9), [("835", 22_000_000, 0), ("103", 0, 22_000_000)], "공동 개발 협력 외주 용역비",
          vendor="가상모빌리티(주)", case="C-R5-02")
    b.add(date(2025, 11, 20), [("831", 1_500_000, 0), ("103", 0, 1_500_000)], "제안서 영문 번역 수수료",
          vendor="가상번역서비스", case="N-R5-01")
    # R6: 7일 이내 같은 금액의 정상 거래(적요 다름) / 8일 간격(탐지되지 않아야 함)
    for d, team, case in [(date(2025, 6, 10), "개발팀", "FP-R6-01"), (date(2025, 6, 17), "영업팀", "FP-R6-01"),
                          (date(2025, 9, 9), "관리팀", "N-R6-01"), (date(2025, 9, 17), "개발팀", "N-R6-01")]:
        topic = "개발자 세미나" if d.month == 6 else "보안 교육"
        b.add(d, [("831", 300_000, 0), ("103", 0, 300_000)], f"{topic} 등록비 - {team}", vendor="가상교육센터",
              case=case)
    return b


def to_frames(b):
    vouchers = sorted(b.vouchers, key=lambda v: (v["posting"], v["seq"]))
    counters, rows, ids = {}, [], {}
    for v in vouchers:
        y = v["posting"].year
        counters[y] = counters.get(y, 0) + 1
        je = f"ITS-{y}-{counters[y]:05d}"
        ids[v["seq"]] = je
        v["je_id"] = je
        for line in v["lines"]:
            code, dr, cr = line[:3]
            desc = line[3] if len(line) > 3 else v["desc"]
            rows.append({"전표번호": je, "회계처리일": v["posting"], "입력일시": v["entry"], "계정코드": code,
                         "계정과목명": ACCOUNTS[code], "차변금액": dr, "대변금액": cr, "거래처": v["vendor"],
                         "적요": desc, "전표원천": v["source"], "전표유형": v["je_type"], "입력자": v["creator"],
                         "승인자": v["approver"]})
    return pd.DataFrame(rows), vouchers


def write_uploads(df):
    UPLOAD.mkdir(parents=True, exist_ok=True)
    text = df.copy()
    text["회계처리일"] = text["회계처리일"].map(lambda d: d.strftime("%Y-%m-%d"))
    text["입력일시"] = text["입력일시"].map(lambda d: d.strftime("%Y-%m-%d %H:%M:%S"))
    text.to_csv(UPLOAD / "가상IT_분개장_2024_2025.csv", index=False, encoding="utf-8-sig")

    xl = df.copy()
    xl["회계처리일"] = pd.to_datetime(xl["회계처리일"])
    with pd.ExcelWriter(UPLOAD / "가상IT_분개장_2024_2025.xlsx", engine="openpyxl") as w:
        xl.to_excel(w, sheet_name="분개장", index=False)
        ws = w.sheets["분개장"]
        w.book.properties.creator = COMPANY + " (가상)"
        w.book.properties.created = w.book.properties.modified = datetime(2026, 1, 1)
        for cell in ws["B"][1:]:
            cell.number_format = "yyyy-mm-dd"
        for cell in ws["C"][1:]:
            cell.number_format = "yyyy-mm-dd hh:mm:ss"
        for col in ("F", "G"):
            for cell in ws[col][1:]:
                cell.number_format = "#,##0"

    single = text.copy()
    single.insert(5, "금액", single["차변금액"] + single["대변금액"])
    single.insert(6, "차대구분", ["차" if d else "대" for d in single["차변금액"]])
    single = single.drop(columns=["차변금액", "대변금액"])
    single.to_csv(UPLOAD / "가상IT_분개장_단일금액_2024_2025.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    from ground_truth_cases import write_ground_truth

    book = build()
    frame, vouchers = to_frames(book)
    write_uploads(frame)
    write_ground_truth(vouchers, TRUTH / "가상IT_정답표.csv")
    print(f"라인 {len(frame):,}행, 전표 {frame['전표번호'].nunique():,}개, "
          f"기간 {frame['회계처리일'].min()} ~ {frame['회계처리일'].max()}")
