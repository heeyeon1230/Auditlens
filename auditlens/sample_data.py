"""가상 분개장 생성기.

가상의 전자부품 제조회사 "가상전자부품(주)"의 전기(2024년)·당기(2025년) 분개장을 만듭니다.
실제 회사·거래처와 관계없는 테스트용 데이터입니다.

- 전표번호는 모두 "SIM-"으로 시작합니다 (SIMulated, 가상).
- 난수 시드를 고정해 몇 번을 실행해도 같은 데이터가 나옵니다.
- 정상 거래와 함께, 탐지 규칙 검증용 "테스트용 비정상 패턴"과
  "정상이지만 드문 거래"(오탐 확인용)를 일부러 넣고, 그 목록을 정답표로 따로 돌려줍니다.
  정답표의 패턴은 탐지 규칙 시험용 장치일 뿐, 부정이나 회계 오류를 뜻하지 않습니다.
- 단순화를 위해 부가가치세 분개는 생략했습니다.
"""

import io
import random
from datetime import date, datetime, timedelta

import pandas as pd

COMPANY_NAME = "가상전자부품(주)"
FICTIONAL_NOTICE = (
    "이 데이터는 AuditLens 시연·테스트용으로 프로그램이 생성한 가상 데이터입니다. "
    "실제 회사, 거래처, 인물과 관계가 없습니다. "
    "일부 전표에는 탐지 규칙 검증을 위한 테스트용 패턴이 의도적으로 들어 있으며, "
    "이는 부정이나 회계 오류를 의미하지 않습니다."
)
JE_PREFIX = "SIM"
PRIOR_YEAR = 2024
CURRENT_YEAR = 2025
DEFAULT_SEED = 20251231

ACCOUNTS = {
    "103": "보통예금",
    "108": "외상매출금",
    "131": "원재료",
    "133": "재공품",
    "135": "제품",
    "206": "기계장치",
    "207": "감가상각누계액",
    "251": "외상매입금",
    "253": "미지급금",
    "254": "예수금",
    "295": "퇴직급여충당부채",
    "404": "제품매출",
    "455": "제품매출원가",
    "501": "원재료비",
    "504": "임금",
    "516": "전력비",
    "518": "감가상각비(제조)",
    "802": "급여",
    "806": "퇴직급여",
    "818": "감가상각비(판관)",
    "819": "임차료",
    "824": "운반비",
    "830": "소모품비",
    "831": "지급수수료",
}

# 거래처는 모두 가상의 이름입니다. (이름, 월평균 거래 건수, 1건 금액 중앙값)
SUPPLIERS = [
    ("가상소재(주)", 9, 12_000_000),
    ("샘플세라믹(주)", 8, 6_000_000),
    ("테스트구리(주)", 7, 9_000_000),
    ("모의기판(주)", 6, 15_000_000),
    ("예시화학(주)", 5, 3_000_000),
]
CUSTOMERS = [
    ("가상모바일(주)", 14, 20_000_000),
    ("샘플오토(주)", 12, 15_000_000),
    ("테스트가전(주)", 10, 10_000_000),
    ("모의통신(주)", 9, 8_000_000),
]
LOGISTICS = ["테스트물류(주)", "모의택배(주)"]
SUPPLY_SHOPS = ["예시문구", "가상공구상사"]

# 입력자 ID (가상)
USER_ACCOUNTING = ["acc_kim", "acc_lee", "acc_park"]
APPROVER = "mgr_choi"


def _business_days(year, month):
    d = date(year, month, 1)
    days = []
    while d.month == month:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _next_business_day(d):
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _prev_business_day(d):
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _amount(rng, median, sigma):
    """금액은 대부분 작고 가끔 큰 '오른쪽으로 치우친' 분포(로그정규분포)로 만듭니다. 10원 단위."""
    value = rng.lognormvariate(0, sigma) * median
    return int(round(value, -1))


class _JournalBuilder:
    """전표를 모아 두었다가 날짜순으로 전표번호를 붙여 표로 만듭니다."""

    def __init__(self, rng):
        self.rng = rng
        self.entries = []

    def add(self, posting_date, lines, description, *, vendor="", source="자동", je_type="일반",
            created_by="batch", approved_by="system", entry_dt=None, key=None):
        """lines: [(계정코드, 차변금액, 대변금액), ...] 차변 합계와 대변 합계가 같아야 합니다."""
        total_dr = sum(dr for _, dr, _ in lines)
        total_cr = sum(cr for _, _, cr in lines)
        if total_dr != total_cr:
            raise ValueError(f"차대 불일치 전표 생성 시도: {description} {total_dr} != {total_cr}")
        if entry_dt is None:
            if source == "자동":
                entry_dt = datetime.combine(posting_date, datetime.min.time()) + timedelta(hours=2)
            else:
                d = _next_business_day(posting_date + timedelta(days=self.rng.randint(0, 2)))
                entry_dt = datetime.combine(d, datetime.min.time()) + timedelta(
                    hours=self.rng.randint(9, 17), minutes=self.rng.randint(0, 59))
        entry = {
            "key": key or f"auto-{len(self.entries)}",
            "posting_date": posting_date,
            "entry_date": entry_dt,
            "lines": lines,
            "description": description,
            "vendor": vendor,
            "source": source,
            "je_type": je_type,
            "created_by": created_by,
            "approved_by": approved_by,
            "order": len(self.entries),
        }
        self.entries.append(entry)
        return entry

    def to_dataframe(self):
        ordered = sorted(self.entries, key=lambda e: (e["posting_date"], e["order"]))
        counters = {}
        rows = []
        key_to_id = {}
        for e in ordered:
            year = e["posting_date"].year
            counters[year] = counters.get(year, 0) + 1
            je_id = f"{JE_PREFIX}-{year}-{counters[year]:06d}"
            key_to_id[e["key"]] = je_id
            for code, dr, cr in e["lines"]:
                rows.append({
                    "je_id": je_id,
                    "posting_date": e["posting_date"],
                    "entry_date": e["entry_date"],
                    "account_code": code,
                    "account_name": ACCOUNTS[code],
                    "debit": dr,
                    "credit": cr,
                    "description": e["description"],
                    "vendor": e["vendor"],
                    "source": e["source"],
                    "je_type": e["je_type"],
                    "created_by": e["created_by"],
                    "approved_by": e["approved_by"],
                })
        df = pd.DataFrame(rows)
        df["posting_date"] = pd.to_datetime(df["posting_date"])
        df["entry_date"] = pd.to_datetime(df["entry_date"])
        return df, key_to_id


def _normal_year(b, year, growth):
    """한 해 동안의 정상 거래를 만듭니다."""
    rng = b.rng
    end_of_data = date(CURRENT_YEAR, 12, 31)
    for month in range(1, 13):
        bdays = _business_days(year, month)
        month_end = bdays[-1]
        purchases_total = 0
        sales_total = 0
        cost_pool = {"504": 0, "516": 0, "518": 0}

        # 1) 원재료 매입 (구매 모듈 자동 전표) → 30일 뒤 대금 지급
        for name, count, median in SUPPLIERS:
            for _ in range(max(1, count + rng.randint(-1, 1))):
                d = rng.choice(bdays)
                amt = _amount(rng, median * growth, 0.35)
                purchases_total += amt
                b.add(d, [("131", amt, 0), ("251", 0, amt)], f"{name} 원재료 매입",
                      vendor=name, created_by="batch_mm")
                pay = _next_business_day(d + timedelta(days=30))
                if pay <= end_of_data:
                    b.add(pay, [("251", amt, 0), ("103", 0, amt)], f"{name} 매입대금 지급",
                          vendor=name, created_by="batch_fi")

        # 2) 제품 매출 (판매 모듈 자동 전표) → 45일 뒤 대금 회수
        for name, count, median in CUSTOMERS:
            for _ in range(max(1, count + rng.randint(-2, 2))):
                d = rng.choice(bdays)
                amt = _amount(rng, median * growth, 0.4)
                sales_total += amt
                b.add(d, [("108", amt, 0), ("404", 0, amt)], f"{name} 제품 매출",
                      vendor=name, created_by="batch_sd")
                col = _next_business_day(d + timedelta(days=45))
                if col <= end_of_data:
                    b.add(col, [("103", amt, 0), ("108", 0, amt)], f"{name} 매출대금 회수",
                          vendor=name, created_by="batch_fi")

        # 3) 급여 (25일, 인사 모듈 자동 전표)
        payday = _prev_business_day(date(year, month, 25))
        wage = _amount(rng, 180_000_000 * growth, 0.03)
        salary = _amount(rng, 90_000_000 * growth, 0.03)
        withholding = int(round((wage + salary) * 0.1, -1))
        b.add(payday, [("504", wage, 0), ("802", salary, 0), ("254", 0, withholding),
                       ("103", 0, wage + salary - withholding)],
              f"{year}년 {month}월 급여", created_by="batch_hr")
        cost_pool["504"] += wage

        # 4) 전력비 (10일 청구, 25일 지급). 여름(7~8월)에 냉방으로 30% 증가
        bill_day = _next_business_day(date(year, month, 10))
        season = 1.3 if month in (7, 8) else 1.0
        power = _amount(rng, 25_000_000 * season * growth, 0.05)
        b.add(bill_day, [("516", power, 0), ("253", 0, power)], f"{month}월 전력요금 청구",
              vendor="가상전력공사", created_by="batch_fi")
        b.add(payday, [("253", power, 0), ("103", 0, power)], f"{month}월 전력요금 납부",
              vendor="가상전력공사", created_by="batch_fi")
        cost_pool["516"] += power

        # 5) 임차료: 매달 같은 금액 (정상이지만 '같은 금액 반복'이라 중복 규칙 오탐 확인용)
        rent_day = bdays[0]
        b.add(rent_day, [("819", 15_000_000, 0), ("103", 0, 15_000_000)], f"{month}월 사무실 임차료",
              vendor="샘플빌딩(주)", source="수기", created_by="acc_lee", approved_by=APPROVER,
              key=f"rent-{year}-{month}")

        # 6) 운반비·소모품비·지급수수료 (회계팀 수기 전표)
        for _ in range(12 + rng.randint(-2, 2)):
            amt = _amount(rng, 700_000, 0.4)
            v = rng.choice(LOGISTICS)
            b.add(rng.choice(bdays), [("824", amt, 0), ("103", 0, amt)], f"{v} 제품 운송비",
                  vendor=v, source="수기", created_by="acc_park", approved_by=APPROVER)
        for _ in range(8 + rng.randint(-2, 2)):
            amt = _amount(rng, 400_000, 0.6)
            v = rng.choice(SUPPLY_SHOPS)
            b.add(rng.choice(bdays), [("830", amt, 0), ("103", 0, amt)], f"{v} 소모품 구입",
                  vendor=v, source="수기", created_by="acc_park", approved_by=APPROVER)
        fee_day = _next_business_day(date(year, month, 5))
        b.add(fee_day, [("831", 2_200_000, 0), ("103", 0, 2_200_000)], f"{month}월 세무기장 수수료",
              vendor="샘플세무회계", source="수기", created_by="acc_lee", approved_by=APPROVER)
        for _ in range(2):
            amt = _amount(rng, 120_000, 0.5)
            b.add(rng.choice(bdays), [("831", amt, 0), ("103", 0, amt)], "송금 수수료",
                  vendor="가상은행", created_by="batch_fi")

        # 7) 월말 원가 계산 (원가 모듈 자동 전표)
        issue = int(round(purchases_total * rng.uniform(0.85, 0.95), -1))
        b.add(month_end, [("501", issue, 0), ("131", 0, issue)], f"{month}월 원재료 출고",
              created_by="batch_co")
        dep_mfg = 40_000_000
        dep_sga = 5_000_000
        b.add(month_end, [("518", dep_mfg, 0), ("818", dep_sga, 0), ("207", 0, dep_mfg + dep_sga)],
              f"{month}월 감가상각비", created_by="batch_co")
        cost_pool["518"] += dep_mfg
        total_mfg = issue + cost_pool["504"] + cost_pool["516"] + cost_pool["518"]
        b.add(month_end, [("133", total_mfg, 0), ("501", 0, issue), ("504", 0, cost_pool["504"]),
                          ("516", 0, cost_pool["516"]), ("518", 0, cost_pool["518"])],
              f"{month}월 제조원가 재공품 대체", created_by="batch_co")
        finished = int(round(total_mfg * rng.uniform(0.93, 0.98), -1))
        b.add(month_end, [("135", finished, 0), ("133", 0, finished)], f"{month}월 완성품 제품 대체",
              created_by="batch_co")
        cogs = int(round(sales_total * rng.uniform(0.70, 0.75), -1))
        b.add(month_end, [("455", cogs, 0), ("135", 0, cogs)], f"{month}월 제품 매출원가",
              created_by="batch_co")

    # 8) 연말 성과급 (정상이지만 금액이 큰 드문 거래, 오탐 확인용)
    bonus_day = _prev_business_day(date(year, 12, 24))
    b.add(bonus_day, [("504", 350_000_000, 0), ("802", 150_000_000, 0), ("103", 0, 500_000_000)],
          f"{year}년 연말 성과급 지급", created_by="batch_hr", key=f"bonus-{year}")

    # 9) 기말 결산 분개: 퇴직급여충당부채 (결산일 당일 입력)
    closing = date(year, 12, 31)
    b.add(closing, [("806", 120_000_000, 0), ("295", 0, 120_000_000)], f"{year}년 퇴직급여충당부채 설정",
          source="수기", je_type="결산", created_by="acc_kim", approved_by=APPROVER,
          entry_dt=datetime(year, 12, 31, 16, 30))


def _inject_patterns(b):
    """당기(2025년)에 테스트용 패턴을 넣고 정답표를 돌려줍니다."""
    y = CURRENT_YEAR
    key_rows = []

    def record(scenario, kind, rules, content, keys):
        key_rows.append({"시나리오": scenario, "구분": kind, "관련 규칙": rules,
                         "내용": content, "keys": keys})

    # A01 (R1) 소모품비 계정에서 평소보다 훨씬 큰 금액
    b.add(date(y, 8, 19), [("830", 46_800_000, 0), ("103", 0, 46_800_000)], "예시문구 소모품 구입",
          vendor="예시문구", source="수기", created_by="acc_park", approved_by=APPROVER, key="A01")
    record("A01", "테스트용 비정상 패턴", "R1",
           "소모품비 1건 46,800,000원 (이 계정의 보통 금액은 수십만 원대)", ["A01"])

    # A02 (R2-a) 평소 소액 거래처의 이례적 대량 매입
    b.add(date(y, 11, 12), [("131", 38_500_000, 0), ("251", 0, 38_500_000)], "예시화학(주) 원재료 매입",
          vendor="예시화학(주)", source="수기", created_by="acc_lee", approved_by=APPROVER, key="A02")
    record("A02", "테스트용 비정상 패턴", "R2",
           "예시화학(주) 매입 38,500,000원 (이 거래처 평소 1건 약 300만 원)", ["A02"])

    # A03 (R2-b) 특정 월(2025년 6월) 운반비 건수 급증
    keys = []
    june = _business_days(y, 6)
    for i in range(36):
        k = f"A03-{i}"
        amt = _amount(b.rng, 700_000, 0.4)
        b.add(b.rng.choice(june), [("824", amt, 0), ("103", 0, amt)], "테스트물류(주) 제품 운송비",
              vendor="테스트물류(주)", source="수기", created_by="acc_park", approved_by=APPROVER, key=k)
        keys.append(k)
    record("A03", "테스트용 비정상 패턴", "R2",
           "2025년 6월 운반비 전표가 평소(월 약 12건)보다 36건 많음", keys)

    # A04 (R3) 결산일 이후 입력한 결산일자 매출 조정
    b.add(date(y, 12, 31), [("108", 280_000_000, 0), ("404", 0, 280_000_000)], "4분기 매출 조정",
          vendor="모의통신(주)", source="수기", je_type="조정", created_by="acc_kim", approved_by=APPROVER,
          entry_dt=datetime(y + 1, 1, 20, 10, 12), key="A04")
    record("A04", "테스트용 비정상 패턴", "R3, R4",
           "회계처리일 2025-12-31, 입력일 2026-01-20인 수기 매출 조정 280,000,000원", ["A04"])

    # A05 (R3) 토요일 밤에 입력한 수기 전표
    b.add(date(y, 9, 12), [("831", 6_500_000, 0), ("103", 0, 6_500_000)], "세무 자문 수수료",
          vendor="샘플세무회계", source="수기", created_by="acc_lee", approved_by=APPROVER,
          entry_dt=datetime(y, 9, 13, 22, 40), key="A05")
    record("A05", "테스트용 비정상 패턴", "R3", "2025-09-13(토) 22:40 입력된 수기 전표", ["A05"])

    # A06 (R4) 평소 전표를 쓰지 않는 사용자가 직접 입력·승인한 매출원가 조정
    b.add(date(y, 10, 27), [("455", 54_000_000, 0), ("135", 0, 54_000_000)], "재고 조정",
          source="수기", je_type="조정", created_by="temp_jung", approved_by="temp_jung", key="A06")
    record("A06", "테스트용 비정상 패턴", "R4",
           "입력자와 승인자가 같고(temp_jung), 이 사용자의 전표는 이 1건뿐", ["A06"])

    # A07 (R4) 적요 없는 수기 전표
    b.add(date(y, 3, 31), [("831", 12_000_000, 0), ("253", 0, 12_000_000)], "",
          source="수기", created_by="acc_park", approved_by=APPROVER, key="A07")
    record("A07", "테스트용 비정상 패턴", "R4", "적요가 비어 있는 수기 전표 12,000,000원", ["A07"])

    # A08 (R5) 기말 2개월에 처음 나타난 거래처와의 큰 거래
    b.add(date(y, 11, 20), [("831", 45_000_000, 0), ("103", 0, 45_000_000)], "경영 자문 수수료",
          vendor="신규컨설팅(가상)", source="수기", created_by="acc_kim", approved_by=APPROVER, key="A08-1")
    b.add(date(y, 12, 15), [("831", 38_000_000, 0), ("103", 0, 38_000_000)], "경영 자문 수수료",
          vendor="신규컨설팅(가상)", source="수기", created_by="acc_kim", approved_by=APPROVER, key="A08-2")
    record("A08", "테스트용 비정상 패턴", "R5",
           "2025-11-20 처음 등장한 거래처에 2건 합계 83,000,000원 지급", ["A08-1", "A08-2"])

    # A09 (R6) 같은 매입을 3일 뒤 한 번 더 입력
    b.add(date(y, 5, 14), [("131", 17_350_000, 0), ("251", 0, 17_350_000)], "모의기판(주) 원재료 매입",
          vendor="모의기판(주)", source="수기", created_by="acc_lee", approved_by=APPROVER, key="A09-1")
    b.add(date(y, 5, 19), [("131", 17_350_000, 0), ("251", 0, 17_350_000)], "모의기판(주) 원재료 매입",
          vendor="모의기판(주)", source="수기", created_by="acc_lee", approved_by=APPROVER, key="A09-2")
    record("A09", "테스트용 비정상 패턴", "R6",
           "모의기판(주) 17,350,000원 매입이 5일 간격으로 두 번 입력됨", ["A09-1", "A09-2"])

    # 정상이지만 드문 거래 (오탐 확인용)
    b.add(date(y, 7, 15), [("206", 850_000_000, 0), ("253", 0, 850_000_000)], "SMT 생산설비 취득",
          vendor="가상설비(주)", source="수기", created_by="acc_kim", approved_by=APPROVER, key="N02")
    record("N01", "정상이지만 드문 거래", "R1 등에서 탐지될 수 있음",
           "매년 12월 연말 성과급 500,000,000원", [f"bonus-{PRIOR_YEAR}", f"bonus-{CURRENT_YEAR}"])
    record("N02", "정상이지만 드문 거래", "R1 등에서 탐지될 수 있음",
           "기계장치 취득 850,000,000원 (연 1회성 투자)", ["N02"])
    record("N03", "정상이지만 드문 거래", "R6에서 걸리면 안 됨",
           "매달 같은 금액(15,000,000원)의 임차료", [f"rent-{CURRENT_YEAR}-{m}" for m in range(1, 13)])
    return key_rows


def generate_sample_journal(seed=DEFAULT_SEED):
    """가상 분개장과 테스트 정답표를 만듭니다.

    반환값: (분개장 DataFrame, 정답표 DataFrame)
    분개장은 한 행이 분개 라인 하나이며, 열 이름은 표준 열 이름(schema.py)입니다.
    """
    rng = random.Random(seed)
    b = _JournalBuilder(rng)
    _normal_year(b, PRIOR_YEAR, growth=1.0)
    _normal_year(b, CURRENT_YEAR, growth=1.08)
    key_rows = _inject_patterns(b)
    journal, key_to_id = b.to_dataframe()

    answer_rows = []
    for row in key_rows:
        ids = sorted(key_to_id[k] for k in row["keys"])
        shown = ", ".join(ids[:3]) + (f" 외 {len(ids) - 3}건" if len(ids) > 3 else "")
        answer_rows.append({"시나리오": row["시나리오"], "구분": row["구분"], "관련 규칙": row["관련 규칙"],
                            "내용": row["내용"], "전표 수": len(ids), "전표번호": shown,
                            "전표번호 전체": ", ".join(ids)})
    answer_key = pd.DataFrame(answer_rows)
    return journal, answer_key


def to_display_columns(journal):
    """표준 열 이름을 한글 열 이름으로 바꾼 표를 돌려줍니다 (파일 저장용)."""
    from auditlens.schema import COLUMN_LABELS
    return journal.rename(columns=COLUMN_LABELS)


def journal_to_csv_bytes(journal):
    """엑셀에서 한글이 깨지지 않도록 UTF-8(BOM 포함)으로 저장합니다."""
    out = to_display_columns(journal).copy()
    out["회계처리일"] = out["회계처리일"].dt.strftime("%Y-%m-%d")
    out["입력일시"] = out["입력일시"].dt.strftime("%Y-%m-%d %H:%M")
    return out.to_csv(index=False).encode("utf-8-sig")


def journal_to_excel_bytes(journal, answer_key=None):
    """첫 시트에 분개장, 둘째 시트에 가상 데이터 안내를 넣은 Excel 파일을 만듭니다."""
    buffer = io.BytesIO()
    out = to_display_columns(journal).copy()
    out["회계처리일"] = out["회계처리일"].dt.date
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        out.to_excel(writer, sheet_name="분개장", index=False)
        notice = pd.DataFrame({"안내": [
            f"[가상 데이터] {COMPANY_NAME}",
            FICTIONAL_NOTICE,
            f"기간: {PRIOR_YEAR}-01-01 ~ {CURRENT_YEAR}-12-31 (전기 {PRIOR_YEAR}년, 당기 {CURRENT_YEAR}년)",
            "전표번호가 SIM-으로 시작하는 모든 전표는 가상입니다.",
        ]})
        notice.to_excel(writer, sheet_name="안내", index=False)
        if answer_key is not None:
            answer_key.to_excel(writer, sheet_name="테스트 정답표", index=False)
    return buffer.getvalue()


def generate_error_example():
    """검증 기능 체험용으로, 형식 오류를 일부러 넣은 작은 CSV를 만듭니다.

    반환값: (CSV bytes, 넣은 오류 설명 목록)
    """
    rows = [
        # 전표번호, 회계처리일, 계정코드, 계정과목명, 차변금액, 대변금액, 적요, 거래처
        ["SIM-ERR-0001", "2025-03-03", "830", "소모품비", "350,000", "", "예시문구 소모품 구입", "예시문구"],
        ["SIM-ERR-0001", "2025-03-03", "103", "보통예금", "", "350,000", "예시문구 소모품 구입", "예시문구"],
        ["SIM-ERR-0002", "2025-13-01", "824", "운반비", "500000", "", "날짜 오류 예시", "테스트물류(주)"],
        ["SIM-ERR-0002", "2025-13-01", "103", "보통예금", "", "500000", "날짜 오류 예시", "테스트물류(주)"],
        ["SIM-ERR-0003", "2025-03-05", "831", "지급수수료", "일십만원", "", "금액 오류 예시", "가상은행"],
        ["SIM-ERR-0003", "2025-03-05", "103", "보통예금", "", "100000", "금액 오류 예시", "가상은행"],
        ["", "2025-03-06", "830", "소모품비", "20000", "", "전표번호 누락 예시", "예시문구"],
        ["SIM-ERR-0004", "2025-03-07", "131", "원재료", "1000000", "", "차대 불일치 예시", "가상소재(주)"],
        ["SIM-ERR-0004", "2025-03-07", "251", "외상매입금", "", "900000", "차대 불일치 예시", "가상소재(주)"],
        ["SIM-ERR-0005", "2025-03-10", "824", "운반비", "300000", "", "중복 행 예시", "모의택배(주)"],
        ["SIM-ERR-0005", "2025-03-10", "824", "운반비", "300000", "", "중복 행 예시", "모의택배(주)"],
        ["SIM-ERR-0005", "2025-03-10", "103", "보통예금", "", "600000", "중복 행 예시", "모의택배(주)"],
        ["SIM-ERR-0006", "2025-03-11", "830", "사무용품비", "50000", "50000", "차대 동시 기재·계정명 불일치 예시", "예시문구"],
    ]
    columns = ["전표번호", "회계처리일", "계정코드", "계정과목명", "차변금액", "대변금액", "적요", "거래처"]
    df = pd.DataFrame(rows, columns=columns)
    explanations = [
        "4·5행: 존재하지 않는 날짜 2025-13-01 (V3, 분석 제외)",
        "6행: 금액에 글자 '일십만원' (V4, 분석 제외)",
        "8행: 전표번호 빈칸 (V5, 분석 제외)",
        "9·10행: 전표 SIM-ERR-0004 차변 1,000,000 / 대변 900,000 (V8)",
        "11·12행: 모든 값이 같은 행 (V9)",
        "14행: 차변·대변 동시 기재 (V6), 계정코드 830의 계정명이 '사무용품비'로 다름 (V11)",
        "2·3행: 쉼표가 들어간 금액 '350,000' (V4 정보: 숫자로 읽음)",
    ]
    return df.to_csv(index=False).encode("utf-8-sig"), explanations
