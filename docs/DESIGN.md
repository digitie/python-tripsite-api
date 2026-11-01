# python-tripsite-api 설계 문서

상태: Draft (적대적 리뷰 반영 전)
작성일: 2026-11-01

## 1. 목표 / 비범위

### 목표
- 야놀자(yanolja.com), 여기어때(goodchoice.kr), 네이버예약(booking.naver.com) 세 곳에
  **개인 계정으로 로그인**하여 **본인의 예약 내역**을 Playwright로 조회하는 Python 라이브러리를 제공한다.
- 위 라이브러리를 [MCP](https://modelcontextprotocol.io) 서버로 감싸, AI 에이전트(Claude 등)가
  표준 MCP tool 호출로 예약 조회를 수행할 수 있게 한다.
- 각 사이트는 공식 공개 API가 없으므로 브라우저 자동화(UI 스크래핑) 방식을 쓴다. DOM 구조는
  사전 통지 없이 바뀔 수 있음을 전제로, 유지보수 비용을 낮추는 구조를 우선한다.

### 비범위 (Out of scope)
- 예약 생성/변경/취소 등 **쓰기 작업**. 이 라이브러리는 조회(read-only)만 수행한다.
- 다수 사용자 계정을 대량으로 수집하는 용도. 어디까지나 "내 계정 하나"를 위한 개인용 도구다.
- CAPTCHA/OTP 자동 우회. 2단계 인증이 뜨면 사람이 개입해야 하는 흐름(headed 모드)을 제공하는
  것으로 그친다. 봇 탐지 우회 기법(지문 스푸핑 등)은 다루지 않는다.
- 사이트별 공식 제휴/파트너 API 연동 (불필요 — 있다면 그쪽을 쓰는 게 맞다).

### 운영 원칙
- 각 사이트의 이용약관을 존중한다: 요청 빈도를 낮게 유지(사람이 쓰는 속도 이상으로 조회하지
  않음), 로그인은 세션을 재사용해 최소화한다.
- 자격증명은 **절대 저장소에 커밋하지 않는다**. 환경변수로만 주입하고, `.gitignore`로
  `storage_state/`, `.env`, `*.secret.json` 등을 원천 배제한다.

## 2. 리포지토리 구조

```
python-tripsite-api/
├── pyproject.toml
├── README.md
├── LICENSE
├── docs/
│   └── DESIGN.md
├── src/
│   └── tripsite_api/
│       ├── __init__.py
│       ├── models.py          # Reservation, ReservationStatus 등 공용 데이터 모델
│       ├── errors.py          # 공용 예외 타입
│       ├── config.py          # Credentials/Settings 로딩 (env var 기반)
│       ├── browser.py         # Playwright 브라우저/컨텍스트 + storage_state 캐시 관리
│       ├── sites/
│       │   ├── __init__.py
│       │   ├── base.py        # BaseSiteClient ABC
│       │   ├── yanolja.py
│       │   ├── yeogiotae.py
│       │   └── naver.py
│       ├── registry.py        # site 이름 -> client 클래스 매핑
│       ├── cli.py             # `tripsite login <site>` 등 1회성 수동 로그인 도구
│       └── mcp_server.py      # MCP 서버 엔트리포인트 (tool 정의)
├── tests/
│   ├── unit/                  # 선택자/파서 단위 테스트 (저장된 HTML fixture 기반)
│   ├── fixtures/
│   │   └── html/              # 익명화된 샘플 HTML (실제 계정 정보 없음)
│   └── e2e/                   # 실제 계정으로 돌리는 live e2e (환경변수 없으면 skip)
└── .github/workflows/ci.yml   # unit 테스트만 실행 (live e2e는 CI에서 기본 skip)
```

## 3. 데이터 모델 (`models.py`)

```python
class ReservationStatus(str, Enum):
    CONFIRMED = "confirmed"      # 예약 확정/완료
    CANCELLED = "cancelled"      # 취소됨
    COMPLETED = "completed"      # 이용 완료(체크아웃 지남)
    UNKNOWN = "unknown"          # 사이트 고유 상태를 매핑 못한 경우

@dataclass(frozen=True)
class Reservation:
    site: str                       # "yanolja" | "yeogiotae" | "naver"
    reservation_id: str             # 사이트 내부 예약번호. 없으면 안정적인 해시로 생성
    property_name: str              # 숙소/매장명
    room_name: str | None
    check_in: date | None
    check_out: date | None
    status: ReservationStatus
    status_label_raw: str           # 사이트에 표시된 원문 상태 텍스트 (예: "예약완료")
    price_amount: int | None        # 원 단위, 파싱 실패 시 None
    price_currency: str = "KRW"
    booked_at: datetime | None = None
    detail_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)  # 파서가 못 담은 나머지 원본 필드
```

설계 원칙:
- **필드를 못 채워도 예외를 던지지 않는다.** 파싱 실패는 `None`/`UNKNOWN`으로 degrade하고,
  `extra`에 원본 텍스트를 남겨 디버깅 가능하게 한다. 사이트 DOM이 바뀌어 일부 필드만 깨졌을 때
  전체 조회가 실패하는 것을 막기 위함.
- 날짜는 가능한 `date`/`datetime`으로 정규화하되, 파싱 불가 시 `None` + `extra["raw_date_text"]`.

## 4. 예외 타입 (`errors.py`)

```python
class TripsiteError(Exception): ...
class LoginRequiredError(TripsiteError): ...      # 세션 만료, 재로그인 필요
class LoginFailedError(TripsiteError): ...        # 자격증명 오류 등으로 로그인 자체 실패
class TwoFactorRequiredError(TripsiteError): ...  # OTP/캡차 등 사람 개입 필요
class ScrapeStructureError(TripsiteError): ...     # 예상 DOM 요소를 못 찾음(사이트 구조 변경 추정)
class CredentialsNotConfiguredError(TripsiteError): ...
```

MCP 서버는 이 예외들을 잡아 tool 응답의 `isError`/에러 메시지로 변환한다(5번 참고).

## 5. 자격증명 / 설정 (`config.py`)

- 사이트별 환경변수 2개: `YANOLJA_ID` / `YANOLJA_PW`, `YEOGIOTAE_ID` / `YEOGIOTAE_PW`,
  `NAVER_ID` / `NAVER_PW`.
- 공통 옵션 환경변수:
  - `TRIPSITE_STATE_DIR` (기본 `~/.tripsite-api/state`): 사이트별 `storage_state` JSON과
    디버그 아티팩트(스크린샷/HTML) 저장 위치.
  - `TRIPSITE_HEADLESS` (기본 `true`): `cli.py` 수동 로그인 시에는 강제로 `false`.
  - `TRIPSITE_NAV_TIMEOUT_MS` (기본 `15000`).
- `Settings.for_site(site: str) -> Credentials`가 env var를 읽고, 없으면
  `CredentialsNotConfiguredError`를 던진다. 비밀번호를 로그에 절대 출력하지 않는다
  (`repr`/`__str__` 오버라이드로 마스킹).
- `storage_state` 파일은 평문 쿠키/토큰을 담으므로 `0600` 권한으로 저장하고, 역시 git에
  올라가지 않도록 리포 루트 밖(`~/.tripsite-api/state`)을 기본값으로 둔다.

## 6. 브라우저 세션 관리 (`browser.py`)

```python
class BrowserSession:
    def __init__(self, site: str, *, headless: bool, state_dir: Path): ...
    async def __aenter__(self) -> "BrowserSession": ...  # playwright 기동, context 생성
    async def __aexit__(self, ...): ...                  # storage_state 저장 후 정리
    async def new_page(self) -> Page: ...
```

- 사이트별로 `storage_state_<site>.json`을 캐시해, 매 호출마다 로그인하지 않고 세션 쿠키를
  재사용한다. 이는 (a) 호출 지연을 줄이고 (b) 반복 로그인으로 인한 이상 탐지 가능성을 줄이기
  위함이다.
- 한 Playwright 프로세스 당 Chromium 하나를 기동하고, MCP 서버 프로세스 생존 기간 동안
  재사용한다(사이트별 BrowserContext만 분리). 서버 종료 시 정리.
- 동시성: 한 사이트에 대해 동시에 2개 이상의 조회가 들어오면 `asyncio.Lock`으로 직렬화한다
  (같은 세션/쿠키를 여러 탭이 동시에 조작하며 생기는 경쟁 상태 방지).
- 재로그인 판단: 예약 페이지로 이동했을 때 로그인 페이지로 리다이렉트되거나 로그인 전용
  마크업이 보이면 `LoginRequiredError`를 세션 계층에서 올리고, 호출자가 `login()`을 1회
  재시도하도록 한다(무한 루프 방지를 위해 재시도는 최대 1회).

## 7. 사이트 어댑터 인터페이스 (`sites/base.py`)

```python
class BaseSiteClient(ABC):
    site_name: ClassVar[str]

    def __init__(self, credentials: Credentials, session: BrowserSession): ...

    @abstractmethod
    async def login(self, page: Page) -> None:
        """자격증명으로 로그인. 2FA/캡차 감지 시 TwoFactorRequiredError."""

    @abstractmethod
    async def is_logged_in(self, page: Page) -> bool: ...

    @abstractmethod
    async def fetch_reservations(self, page: Page, *, limit: int | None) -> list[Reservation]:
        """로그인된 상태를 전제로 예약 목록 페이지를 순회/파싱."""

    async def get_reservations(self, *, limit: int | None = None) -> list[Reservation]:
        """login 상태 확인 -> 필요시 login() -> fetch_reservations() 까지 묶는 공개 메서드.
        BaseSiteClient에서 템플릿 메서드로 구현하고, 하위 클래스는 위 3개 추상 메서드만 구현."""
```

- 각 사이트 모듈은 선택자(selector)를 **모듈 상단의 상수 딕셔너리**로 모아둔다
  (`SELECTORS = {...}`). DOM 변경 대응 시 코드 로직을 건드리지 않고 상수만 고치면 되게 하기
  위함이며, 이 상수는 `tests/unit`의 fixture HTML로 검증한다.
- `fetch_reservations`가 예상 요소를 못 찾으면 바로 실패시키지 않고: (1) 현재 페이지
  스크린샷 + HTML을 `TRIPSITE_STATE_DIR/debug/<site>/<timestamp>.*`에 저장, (2)
  `ScrapeStructureError(screenshot_path=..., html_path=...)`를 던진다. 사람이 로그만 보고도
  바로 원인 파악 + 선택자 수정이 가능하게 하기 위함.

## 8. 사이트별 노트 (조사 결과 + 불확실성 명시)

이 프로젝트의 가장 큰 리스크는 "실제 로그인 후 예약내역 페이지의 정확한 URL/DOM 구조"를
사전에 100% 확정할 수 없다는 점이다 (공개 API 문서가 없고, 개발 환경의 네트워크 정책상 현재는
해당 사이트들에 직접 접속해 사전 조사를 하지 못했다 — 구현 단계에서 네트워크 허용 후 실측
예정). 아래는 설계 시점의 가정이며, 구현 착수 시 가장 먼저 실측하여 본 문서를 갱신한다.

### 8.1 야놀자 (yanolja.com)
- 로그인: `https://www.yanolja.com` 상단 로그인 → ID/PW 로그인 폼 또는 소셜 로그인(카카오/
  네이버/구글). 이 라이브러리는 **ID/PW 자체 로그인 폼만** 지원한다(소셜 로그인은 각 소셜
  서비스의 OAuth 흐름까지 자동화해야 해서 범위를 벗어남 — README에 한계로 명시).
- 예약내역: 로그인 후 마이페이지 내 "예약내역" 메뉴. 정확한 경로는 구현 착수 시 실측.
- 리스크: SPA 렌더링(React 등)으로 추정되며, 요소 대기는 `networkidle`보다
  `page.wait_for_selector`를 우선한다(SPA는 지속적 polling/analytics 요청으로 networkidle이
  영원히 안 올 수 있음).

### 8.2 여기어때 (goodchoice.kr, 구 yeogiotae.com)
- 공식 "여기어때" 서비스 도메인은 `goodchoice.kr`로 이전되었다는 정보가 있으나, 리다이렉트
  구조(`yeogiotae.com` → `goodchoice.kr`)는 구현 착수 시 실측 필요.
- 앱스토어 메타데이터상 "예약내역" 기능이 뚜렷이 노출된 것은 사장님(파트너)용 앱이며, 일반
  소비자용 예약내역은 일반 웹/앱의 마이페이지 하위에 있을 것으로 추정. **이 라이브러리는
  일반 소비자 계정 기준으로만 구현**한다.
- 로그인 수단도 ID/PW 외 소셜 로그인이 있을 수 있어 8.1과 동일하게 ID/PW만 지원.

### 8.3 네이버예약 (booking.naver.com)
- 네이버는 통합 계정(nid.naver.com)으로 로그인하므로, 로그인 폼 자체는 네이버 공통 로그인
  페이지를 사용한다. 네이버는 자동화 로그인에 대한 보안 챌린지(기기 인증, 캡차)가 특히
  적극적인 것으로 알려져 있어, 세션 재사용(storage_state)의 중요성이 다른 두 사이트보다
  크다.
- 예약내역은 모바일 웹 가이드 기준 `m.booking.naver.com/my/bookings` 류의 경로가 언급되나
  비공식 출처라 실측 필요. 네이버페이 주문/예약 내역(`order.pay.naver.com`)이 더 안정적인
  경로일 가능성도 있어 구현 착수 시 두 경로를 모두 확인 후 택일한다.
- 리스크: 네이버는 챌린지 발생 시 `TwoFactorRequiredError`로 명확히 구분해 올려, 호출자가
  "사람 개입이 필요하다"는 신호를 받을 수 있게 한다.

> 구현 작업자는 Task #3/#4/#5(사이트별 어댑터) 착수 시, 먼저 `TRIPSITE_HEADLESS=false`로
> `cli.py login <site>`를 실제 계정으로 1회 실행해 로그인/예약 페이지의 실제 URL·DOM을
> 확보하고, 그 결과로 본 섹션과 `SELECTORS` 상수를 갱신하는 것을 **첫 서브태스크**로 한다.

## 9. MCP 서버 (`mcp_server.py`)

Python 공식 `mcp` SDK(FastMCP)를 사용해 stdio 기반 MCP 서버로 구현한다.

### Tools
| Tool | 입력 | 출력 | 설명 |
|---|---|---|---|
| `list_supported_sites` | (없음) | `["yanolja","yeogiotae","naver"]` | 지원 사이트 목록 |
| `get_reservations` | `site: str`, `limit?: int` | `Reservation[]` (JSON) | 특정 사이트 예약 조회 |
| `get_all_reservations` | `limit?: int` | `{site: Reservation[]}` | 자격증명이 설정된 모든 사이트 조회(병렬) |
| `check_login_status` | `site: str` | `{"logged_in": bool}` | 캐시된 세션이 유효한지 확인(재로그인 없이) |

- 자격증명이 설정되지 않은 사이트는 `get_all_reservations`에서 조용히 스킵하고 결과에
  `{"site": ..., "skipped_reason": "credentials_not_configured"}`로 표시한다(에이전트가
  "왜 이 사이트는 없지?"를 바로 알 수 있게).
- 모든 tool의 에러는 7번 예외 타입을 사람이 읽을 수 있는 메시지로 변환해 MCP 에러 응답으로
  반환한다. 특히 `TwoFactorRequiredError`는 "사람이 `tripsite login <site>`를 headed 모드로
  직접 실행해야 함"을 메시지에 명시한다.
- 서버는 비밀번호/쿠키 값을 tool 응답이나 로그에 절대 포함하지 않는다.

### 트랜스포트
- 기본은 stdio (Claude Desktop/Code의 로컬 MCP 서버 등록 방식과 호환).
- 추후 필요 시 SSE/streamable-http 추가는 범위 밖(필요해지면 별도 설계).

## 10. CLI (`cli.py`)

- `tripsite login <site>`: headed 브라우저로 로그인 페이지를 띄우고, 사람이 ID/PW 입력 +
  2FA/캡차까지 직접 처리한 뒤 Enter를 누르면 `storage_state`를 저장. (자동 ID/PW 주입도
  지원하되, 2FA가 뜨면 멈추고 사람 입력을 기다린다.)
- `tripsite check <site>`: 캐시된 세션으로 로그인 상태만 확인.
- `tripsite reservations <site>`: 콘솔에서 바로 조회 결과 출력(디버깅/수동 확인용).

## 11. 테스트 전략

- **단위 테스트**: 각 사이트 파서를 저장된 HTML fixture(`tests/fixtures/html/<site>/*.html`,
  개인정보 제거/익명화)로 테스트. CI에서 항상 실행.
- **live e2e 테스트**: `tests/e2e/test_<site>.py`. 해당 사이트의 env var(`YANOLJA_ID` 등)가
  없으면 `pytest.skip`. 사용자가 제공한 실제 계정으로, 구현 단계마다 1회 실행해 PR 머지 전
  게이트로 사용한다. CI(GitHub Actions)에는 기본적으로 올리지 않는다(사이트 차단/약관 리스크
  + 매 PR마다 실제 계정으로 로그인하는 것은 과도함). 필요 시 수동 트리거 워크플로만 둔다.
- **PR 게이트**: 각 구현 task는 "2인 적대적 코드리뷰 → 반영 → 해당 사이트 live e2e 1회 통과"
  순서를 거친 뒤에만 머지한다.

## 12. 보안 체크리스트

- [ ] `.gitignore`에 `.env`, `*.secret.json`, `~/.tripsite-api` 상당 경로, `debug/` 포함
- [ ] 비밀번호는 어떤 예외 메시지/로그에도 노출되지 않음 (`Credentials.__repr__` 마스킹)
- [ ] `storage_state` 파일은 리포 바깥 디렉터리에만 저장
- [ ] README에 "개인 계정 전용, 과도한 호출 금지, 각 사이트 ToS 확인 책임은 사용자에게 있음"
      명시
- [ ] 커밋 전 `git diff`/`git status`로 실수로 추가된 state/env 파일 여부 확인

## 13. 구현 로드맵 (Task 매핑)

1. Task #2: `models.py`, `errors.py`, `config.py`, `browser.py`, `sites/base.py`,
   `registry.py`, `.gitignore`, `pyproject.toml`, CI 뼈대.
2. Task #3: 야놀자 어댑터 (+ 실측으로 본 문서 8.1 갱신).
3. Task #4: 여기어때 어댑터 (+ 8.2 갱신).
4. Task #5: 네이버예약 어댑터 (+ 8.3 갱신).
5. Task #6: `mcp_server.py` + `cli.py`.
6. Task #7: README 마무리.

## 14. Open Questions (리뷰어가 특히 검증해야 할 지점)

- 각 사이트 실제 로그인 폼이 ID/PW 단일 폼인지, 아이디/비번 단계가 분리된 2-step인지
  (네이버는 2-step으로 알려짐) — 실측 전까지는 어댑터가 두 패턴 모두 처리할 수 있게
  `login()` 구현을 단계별로 쪼개 두는 것이 맞는가?
- `get_all_reservations`의 "병렬" 실행이 사이트별 `asyncio.Lock`과 상충하지 않는가(사이트가
  다르면 서로 다른 Lock이라 병렬 가능해야 함 — 구현 시 확인 필요).
- 세션 만료 판단을 "로그인 페이지로 리다이렉트"만으로 하는 것이 충분한가, 아니면 네이버처럼
  "정상 페이지인데 특정 배너/모달로 재인증을 요구"하는 패턴도 있는가?
- MCP tool의 `get_reservations`가 사이트당 과거 몇 건/몇 개월까지 조회해야 "충분"한가 (기본
  `limit` 값과 전체 조회 상한을 문서에 명시해야 함).
