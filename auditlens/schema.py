"""표준 열 정의.

업로드한 파일의 열 이름은 회사·ERP마다 다르므로, 프로그램 안에서는 아래의
"표준 열 이름"으로 통일해서 다룹니다. 설계서 5절의 표를 코드로 옮긴 것입니다.
"""

# 표준 열 이름 → 화면에 보여 줄 한글 이름
COLUMN_LABELS = {
    "je_id": "전표번호",
    "posting_date": "회계처리일",
    "account_code": "계정코드",
    "account_name": "계정과목명",
    "debit": "차변금액",
    "credit": "대변금액",
    "entry_date": "입력일시",
    "description": "적요",
    "vendor": "거래처",
    "source": "전표 원천(자동/수기)",
    "je_type": "전표 유형",
    "created_by": "입력자",
    "approved_by": "승인자",
}

# 없으면 분석을 시작할 수 없는 열
REQUIRED_COLUMNS = ["je_id", "posting_date", "account_code", "account_name", "debit", "credit"]

# 있으면 일부 탐지 규칙에 쓰이는 열
OPTIONAL_COLUMNS = [
    "entry_date", "description", "vendor", "source", "je_type", "created_by", "approved_by",
]

# 열 이름 자동 연결용 별칭. 비교할 때는 공백·밑줄을 지우고 소문자로 바꿔서 비교합니다.
COLUMN_ALIASES = {
    "je_id": ["je_id", "전표번호", "전표no", "전표 번호", "journal id", "journal_id", "entry id", "document no"],
    "posting_date": ["posting_date", "회계처리일", "회계일자", "전기일", "전표일자", "일자", "posting date", "gl date"],
    "account_code": ["account_code", "계정코드", "계정 코드", "account code", "gl account"],
    "account_name": ["account_name", "계정과목명", "계정과목", "계정명", "account name"],
    "debit": ["debit", "차변금액", "차변", "debit amount", "dr"],
    "credit": ["credit", "대변금액", "대변", "credit amount", "cr"],
    "entry_date": ["entry_date", "입력일시", "입력일", "등록일", "entry date", "created date"],
    "description": ["description", "적요", "내용", "memo"],
    "vendor": ["vendor", "거래처", "거래처명", "customer/vendor", "partner"],
    "source": ["source", "전표원천", "전표 원천(자동/수기)", "원천", "입력구분"],
    "je_type": ["je_type", "전표유형", "전표 유형", "entry type"],
    "created_by": ["created_by", "입력자", "작성자", "created by", "user"],
    "approved_by": ["approved_by", "승인자", "approved by", "approver"],
}


def _normalize(name: str) -> str:
    return str(name).strip().lower().replace(" ", "").replace("_", "")


def suggest_mapping(file_columns):
    """파일의 열 이름을 보고 표준 열과의 연결을 추측합니다.

    반환값: {표준 열 이름: 파일 열 이름 또는 None}
    한 파일 열은 표준 열 하나에만 연결합니다.
    """
    normalized = {_normalize(c): c for c in file_columns}
    mapping = {}
    used = set()
    for std_col, aliases in COLUMN_ALIASES.items():
        mapping[std_col] = None
        for alias in aliases:
            match = normalized.get(_normalize(alias))
            if match is not None and match not in used:
                mapping[std_col] = match
                used.add(match)
                break
    return mapping
