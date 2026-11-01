# python-tripsite-api 설계 문서

상태: Reviewed v2 (2인 적대적 리뷰 반영 완료)
작성일: 2026-11-01

> v2 변경 요약: 재로그인 책임 분리 명확화, 디버그 덤프 비밀번호 마스킹, 브라우저/Lock
> 소유권을 registry.py로 명시, 페이지네이션·rate limiting·계정 잠김 리스크 대응,
> 비숙박 예약을 위한 데이터 모델 확장, MCP 구조화 에러, 브라우저 바이너리 설치 안내,
> CLI 2FA 흐름과 어댑터 예외 설계 통일. (리뷰 상세는 PR #1 코멘트 참고)

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
- **계정 잠김 리스크를 명시적으로 인지한다.** 야놀자/여기어때/네이버 모두 상용 서비스로서
  디바이스 핑거프린팅, IP 평판, 로그인 실패 횟수 기반 차단/캡차 강화를 할 수 있다. 이
  라이브러리는 그런 봇 탐지를 우회하지 않으며(비범위, 1절 참고), 대신 "실패를 누적시키지
  않는" 보수적 기본값을 가진다: 사이트별 최소 재로그인 간격(쿨다운)과 하루 최대 로그인
  시도 횟수를 설정값으로 둔다(5절). 사용자는 비정상적으로 자주 호출할 경우 계정이
  제한될 수 있음을 README에서 명확히 안내받는다(12절).

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

class ReservationCategory(str, Enum):
    LODGING = "lodging"      # 숙박 (check_in/check_out 사용)
    ACTIVITY = "activity"    # 액티비티/투어 (visit_at 사용)
    DINING = "dining"        # 식당 웨이팅/예약 (visit_at 사용)
    OTHER = "other"          # 분류 불가 (사이트 고유 유형)

@dataclass(frozen=True)
class Reservation:
    site: str                       # "yanolja" | "yeogiotae" | "naver"
    reservation_id: str             # 사이트 내부 예약번호. 없으면 안정적인 해시로 생성
    category: ReservationCategory   # 숙박 vs 비숙박 구분 (여기어때/네이버에 비숙박 존재)
    property_name: str              # 숙소/매장명
    room_name: str | None
    check_in: date | None           # 숙박(LODGING)에서만 사용
    check_out: date | None          # 숙박(LODGING)에서만 사용
    visit_at: datetime | None       # 비숙박(ACTIVITY/DINING/OTHER)의 단일 이용 시각
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
- `category`로 숙박/비숙박을 구분해, 액티비티·식당 등 체크인/체크아웃 개념이 없는 예약은
  `check_in`/`check_out`을 모두 `None`으로 두고 `visit_at`만 채운다. 어댑터가 사이트 응답에서
  분류를 못 하면 `OTHER` + `extra["raw_type_text"]`로 degrade한다.

## 4. 예외 타입 (`errors.py`)

```python
class TripsiteError(Exception): ...
class LoginRequiredError(TripsiteError): ...      # 세션 만료, 재로그인 필요
class LoginFailedError(TripsiteError): ...        # 자격증명 오류 등으로 로그인 자체 실패
class TwoFactorRequiredError(TripsiteError): ...  # OTP/캡차 등 사람 개입 필요
class ScrapeStructureError(TripsiteError): ...     # 예상 DOM 요소를 못 찾음(사이트 구조 변경 추정)
class CredentialsNotConfiguredError(TripsiteError): ...
```

MCP 서버는 이 예외들을 잡아 tool 응답의 `isError`/에러 메시지로 변환한다(9절 참고).

## 5. 자격증명 / 설정 (`config.py`)

- 사이트별 환경변수 2개: `YANOLJA_ID` / `YANOLJA_PW`, `YEOGIOTAE_ID` / `YEOGIOTAE_PW`,
  `NAVER_ID` / `NAVER_PW`.
- 공통 옵션 환경변수:
  - `TRIPSITE_STATE_DIR` (기본 `~/.tripsite-api/state`): 사이트별 `storage_state` JSON과
    디버그 아티팩트(스크린샷/HTML) 저장 위치.
  - `TRIPSITE_HEADLESS` (기본 `true`): `cli.py` 수동 로그인 시에는 강제로 `false`.
  - `TRIPSITE_NAV_TIMEOUT_MS` (기본 `15000`).
  - `TRIPSITE_MIN_LOGIN_INTERVAL_SEC` (기본 `60`): 같은 사이트에 대한 재로그인 시도 사이 최소
    간격. 이 간격 내 재시도는 즉시 `LoginFailedError`로 거절한다(실패 누적/계정 잠김 방지).
  - `TRIPSITE_MAX_LOGIN_ATTEMPTS_PER_DAY` (기본 `5`, 사이트별): 초과 시 그날은 재로그인을
    시도하지 않고 `LoginFailedError`로 즉시 실패시킨다. 시도 횟수는
    `TRIPSITE_STATE_DIR/login_attempts_<site>.json`에 기록.
  - `TRIPSITE_MIN_QUERY_INTERVAL_SEC` (기본 `30`): 같은 사이트에 대한 `fetch_reservations`
    재호출 최소 간격. 이 간격 내 재호출은 마지막 결과를 그대로 캐시에서 반환한다(6절).
- `LoginThrottle.check_and_record()`의 읽기-수정-쓰기는 `fcntl.flock`으로 보호되는 하나의
  크리티컬 섹션이다. 같은 프로세스 내 동시 호출뿐 아니라, MCP 서버와 `cli.py`가 동일
  `TRIPSITE_STATE_DIR`를 공유하는 두 개의 서로 다른 프로세스(9절의 운영 전제)가 동시에
  같은 사이트의 로그인을 기록하려는 경우까지 보호 범위에 들어간다.
- `credentials_for_site(site: str) -> Credentials`가 env var를 읽고, 없으면
  `CredentialsNotConfiguredError`를 던진다. 비밀번호 필드는 `str`이 아니라 `SecretStr`
  래퍼 타입으로 감싼다 - `Credentials.__repr__`/`__str__` 오버라이드만으로는
  `dataclasses.asdict()`/`vars()`/`__dict__` 같은 경로를 가리지 못하기 때문이다(이 함수들은
  dataclass의 `__repr__`을 거치지 않고 필드 값을 그대로 꺼낸다). `SecretStr`은 dataclass가
  아니므로 `asdict()`가 그 내부를 분해하지 못하고 객체 자체를 남기며, 그 객체의 `repr`이
  마스킹되어 있어 어떤 경로로 출력되더라도 평문이 드러나지 않는다. 비밀번호 원문은
  `credentials.password.get_secret_value()`로만 꺼낸다(사이트 어댑터의 `login()`에서
  `page.fill()` 등에 전달할 때).
- `storage_state` 파일은 평문 쿠키/토큰을 담으므로 `0600` 권한으로 저장하고, 역시 git에
  올라가지 않도록 리포 루트 밖(`~/.tripsite-api/state`)을 기본값으로 둔다. 암호화는 v1
  범위에는 없으나, README에 "OS 백업 도구가 이 디렉터리를 평문으로 백업하지 않도록 주의"
  경고를 남긴다(12절). OS 키체인 연동은 향후 고려사항으로 14절에 남긴다.

## 6. 브라우저 세션 관리 (`browser.py`)

```python
class BrowserSession:
    def __init__(self, site: str, *, headless: bool, state_dir: Path, nav_timeout_ms: int = 15_000): ...
    async def __aenter__(self) -> "BrowserSession": ...  # playwright 기동, context 생성
    async def __aexit__(self, ...): ...                  # storage_state 저장 후 정리
    async def new_page(self) -> Page: ...                # 항상 try/finally로 close 책임은 호출자
    async def is_connected(self) -> bool: ...             # health check
    async def restart(self) -> None: ...                  # 끊긴 browser/context 재기동
```

`is_connected()`/`restart()` 체크는 `new_page()` 내부에서 자체적으로 수행한다(아래 "소유권"
문단이 말하는 것은 *세션 인스턴스*의 소유권이며, 매 호출 전 health check를 명령하는 주체가
`SiteRegistry`라는 뜻은 아니다 - 레지스트리는 세션을 1개만 만들어 쥐고 있을 뿐, 그 세션이
스스로 재기동을 판단한다).

- 사이트별로 `storage_state_<site>.json`을 캐시해, 매 호출마다 로그인하지 않고 세션 쿠키를
  재사용한다. 이는 (a) 호출 지연을 줄이고 (b) 반복 로그인으로 인한 이상 탐지 가능성을 줄이기
  위함이다.
- 한 Playwright 프로세스 당 Chromium 하나를 기동하고, MCP 서버 프로세스 생존 기간 동안
  재사용한다(사이트별 BrowserContext만 분리). 서버 종료 시 정리.
- **소유권(ownership)**: `BrowserSession`과 그에 대응하는 `asyncio.Lock`은 개별 호출마다
  새로 만들지 않는다. `registry.py`가 `{site: (BrowserSession, asyncio.Lock)}` 싱글턴
  레지스트리를 프로세스 전역으로 들고 있고, `mcp_server.py`와 `cli.py` 모두 이 레지스트리를
  통해서만 세션/락에 접근한다. 사이트가 다르면 서로 다른 Lock이므로 `get_all_reservations`의
  사이트 간 병렬 실행과 상충하지 않는다(같은 사이트 동시 호출만 직렬화됨). Task #2에서 이
  레지스트리 구현과 함께 "사이트 간 Lock 독립성 + 단일 Chromium 프로세스의 동시 페이지
  생성 시 자원 경합(CPU/메모리)" 동작을 실제로 검증하는 것을 서브태스크로 둔다(13절).
- **재로그인 책임은 BrowserSession이 아니라 BaseSiteClient 한 곳에만 둔다.**
  `BrowserSession`/`new_page()`는 로그인 필요 여부를 스스로 판단하지 않는다. 오직
  `BaseSiteClient` 계층(7절)에서 페이지 이동 후 로그인 페이지 마크업/리다이렉트를 감지해
  `LoginRequiredError`를 올리고, 그 예외를 잡아 `login()`을 호출한 뒤 **같은 동작을 1회만**
  재시도하는 루프는 오직 `get_reservations()` 템플릿 메서드 하나에만 존재한다. 이는
  `fetch_reservations()`가 페이지네이션 중간에 세션이 끊겨 `LoginRequiredError`를 올리는
  경우에도 동일하게 적용된다 — `fetch_reservations()`는 몇 페이지째 읽다가 끊겼든 처음부터
  다시 시도되며, 재시도 1회 한도는 이 전체 호출 단위로 센다(무한 루프 방지).
- 브라우저 크래시/리소스 누수 대응: `BrowserSession.new_page()`가 호출될 때마다
  `is_connected()`로 상태를 확인하고, 끊겨 있으면 `restart()`로 재기동한다. `close()`의
  각 정리 단계(storage_state 저장, context/browser 종료)는 이미 죽은 핸들을 다루는
  `restart()` 경로에서도 안전하도록 예외를 삼키지만, 완전히 무음은 아니고
  `logger.debug`/`logger.warning`으로 남긴다. `new_page()`로 연 페이지는 호출자가
  `try/finally`로 반드시 `close()`하는 것을 계약으로 명시한다(열린 채 방치된 페이지가
  누적되는 것을 막기 위함).
- `SiteRegistry.session_for()`는 사이트별 세션을 최초 생성할 때 `await session.start()`로
  await하는 구간이 있어, 동시에 같은 사이트에 대한 첫 호출이 두 번 들어오면 둘 다 "아직 없음"
  을 보고 세션을 중복 생성할 수 있다(하나는 레지스트리에서 참조를 잃어 Chromium 프로세스가
  누수). 이를 막기 위해 `session_for()`는 생성 구간 전체를 레지스트리 전역의
  `asyncio.Lock`(`_creation_lock`) 하나로 감싼다. 이 락은 "사이트당 최초 1회뿐인 생성"만
  직렬화하므로, 이미 생성된 세션에 대한 이후의 정상 조회/락(`lock_for(site)`)에는 영향이
  없고 "사이트 간 독립적으로 동작한다"는 설계 원칙을 깨지 않는다.

## 7. 사이트 어댑터 인터페이스 (`sites/base.py`)

```python
class BaseSiteClient(ABC):
    site_name: ClassVar[str]
    MAX_RESERVATIONS_UNBOUNDED: ClassVar[int] = 500  # limit=None일 때의 하드 상한

    def __init__(
        self,
        credentials: Credentials,
        session: BrowserSession,
        settings: Settings,
        *,
        lock: asyncio.Lock | None = None,
    ): ...

    @abstractmethod
    async def login(self, page: Page) -> None:
        """자격증명으로 로그인을 시도하고 끝낸다(블로킹 대기 없음).
        2FA/캡차를 감지하면 즉시 TwoFactorRequiredError를 던진다 — "멈춰서 사람 입력을
        기다리는" 동작은 이 메서드의 책임이 아니다(10절 CLI가 예외를 잡아 재시도 루프를
        돈다). 네이버처럼 아이디 입력 -> 다음 화면 -> 비밀번호 입력의 2-step 로그인은
        내부적으로 protected 훅 `_enter_identifier(page)` / `_enter_password(page)`로
        쪼개 구현할 수 있다(단일 폼 사이트는 `login()` 안에서 그냥 순서대로 호출).
        로그인 단계에서 ScrapeStructureError가 발생하면, 디버그 덤프 전에 반드시
        `input[type=password]` 등 비밀번호 필드의 value를 마스킹한다(평문 비밀번호가
        디버그 HTML에 남는 것을 막기 위함 — login() 전용 덤프 헬퍼를 base.py에 둔다)."""

    @abstractmethod
    async def is_logged_in(self, page: Page) -> bool: ...

    @abstractmethod
    async def fetch_reservations(self, page: Page, *, limit: int | None) -> list[Reservation]:
        """로그인된 상태를 전제로 예약 목록 페이지를 순회/파싱한다.
        - `limit`이 주어지면 그 건수만큼 모으는 즉시 순회를 멈춘다.
        - `limit=None`(전체 조회)이어도 `MAX_RESERVATIONS_UNBOUNDED`를 넘기지 않는다
          (상한 없는 전체 수집은 1절의 "사람이 쓰는 속도 이상으로 조회하지 않음" 원칙과
          충돌하므로 금지).
        - 다음 페이지로 넘어가는 방식(버튼/무한스크롤/쿼리파라미터)은 사이트마다 다르므로,
          선택적 protected 훅 `_has_next_page(page) -> bool` /
          `_go_to_next_page(page) -> None`으로 구현하고, 공용 순회 루프는
          `BaseSiteClient`에 템플릿으로 둔다.
        - 중간에 세션이 끊겨 로그인 페이지로 리다이렉트된 것을 감지하면
          `LoginRequiredError`를 던진다(재시도는 이 메서드가 아니라 `get_reservations()`의
          책임 — 아래 참고). 이미 모은 페이지 결과는 버려지고 처음부터 재시도된다."""

    async def get_reservations(self, *, limit: int | None = None) -> list[Reservation]:
        """login 상태 확인 -> 필요시 login() -> fetch_reservations() 까지 묶는 공개 메서드.
        BaseSiteClient에서 템플릿 메서드로 구현하고, 하위 클래스는 위 3개 추상 메서드만
        구현한다. **재로그인 재시도 루프는 이 메서드에만 존재한다**: `is_logged_in()`이
        False거나 `fetch_reservations()`가 `LoginRequiredError`를 던지면 `login()`을
        호출한 뒤 `fetch_reservations()`를 처음부터 1회만 재시도한다(그래도 실패하면
        예외를 그대로 전파). `BrowserSession`은 이 판단에 관여하지 않는다(6절).
        이 메서드는 호출 전체를 생성자에서 받은 `lock`(기본값은 인스턴스 전용 Lock)으로
        감싸므로, 같은 클라이언트 인스턴스에 대한 동시 호출이 캐시 판단과 브라우저 조작을
        동시에 밟는 레이스를 막는다. `fetch_reservations()`가 반환한 결과가 `limit=None`
        인데도 `MAX_RESERVATIONS_UNBOUNDED`를 넘으면 **이 메서드가** 그 자리에서 잘라낸다
        - 각 사이트 어댑터가 상한을 스스로 지키는 것에 의존하지 않는다."""
```

`_dump_debug_artifacts(page, *, label, mask_password)`는 스크린샷/HTML을 저장하고
`ScrapeStructureError`를 **반환만** 한다(테스트에서 내용을 들여다보기 쉽게 하기 위함).
실제 사이트 어댑터는 그 반환값을 그냥 버리기 쉬우므로, 항상 예외를 직접 던지는
`_raise_structure_error(page, *, label, mask_password) -> NoReturn` 쪽을 호출해야 한다.

- 각 사이트 모듈은 선택자(selector)를 **모듈 상단의 상수 딕셔너리**로 모아둔다
  (`SELECTORS = {...}`). DOM 변경 대응 시 코드 로직을 건드리지 않고 상수만 고치면 되게 하기
  위함이며, 이 상수는 `tests/unit`의 fixture HTML로 검증한다. 다만 이 fixture 테스트는
  "선택자가 저장된 샘플 HTML을 여전히 파싱할 수 있는가"만 보장하며, 실제 사이트가 바뀌었는지
  **사전에** 알려주지는 못한다(fixture는 고정이므로 드리프트를 감지할 수 없음) — 선제 탐지는
  11절의 주기적 live e2e로 보완한다.
- `fetch_reservations`가 예상 요소를 못 찾으면 바로 실패시키지 않고: (1) 현재 페이지
  스크린샷 + HTML을 `TRIPSITE_STATE_DIR/debug/<site>/<timestamp>.*`에 저장, (2)
  `ScrapeStructureError(screenshot_path=..., html_path=...)`를 던진다. 사람이 로그만 보고도
  바로 원인 파악 + 선택자 수정이 가능하게 하기 위함. (로그인 단계의 덤프는 위 `login()`
  설명대로 비밀번호 필드를 마스킹한 뒤 저장한다.)

## 8. 사이트별 노트 (조사 결과 + 불확실성 명시)

이 프로젝트의 가장 큰 리스크는 "실제 로그인 후 예약내역 페이지의 정확한 URL/DOM 구조"를
사전에 100% 확정할 수 없다는 점이다 (공개 API 문서가 없고, 개발 환경의 네트워크 정책상 현재는
해당 사이트들에 직접 접속해 사전 조사를 하지 못했다 — 구현 단계에서 네트워크 허용 후 실측
예정). 아래는 설계 시점의 가정이며, 구현 착수 시 가장 먼저 실측하여 본 문서를 갱신한다.

### 8.1 야놀자 (yanolja.com) — Task #3에서 실측 완료 (단, 인증 후 예약목록 마크업은 미확인)
- 실제 서비스 도메인은 `nol.yanolja.com`(Next.js SPA)이며, `www.yanolja.com`은 쓰지 않는다.
  Playwright 기본 UA로 접속하면 403이 뜨므로, `browser.py`의 `BrowserSession.start()`가
  모든 사이트 공통으로 데스크톱 Chrome `User-Agent` + `locale="ko-KR"`를 `new_context()`에
  적용하도록 고쳤다(한 사이트에서 발견했지만 세 사이트 모두 비슷한 소비자 서비스라 공통
  적용이 합리적이라고 판단; 여기어때/네이버에서 다른 값이 필요하면 Task #4/#5에서 조정).
- 로그인: `https://nol.yanolja.com/login` → `https://accounts.yanolja.com/v3/login?...`로
  리다이렉트. 소셜 로그인(카카오/네이버/구글/애플) 버튼들과 "메일로 시작하기" 버튼이 있고,
  이 라이브러리는 **"메일로 시작하기"(ID/PW) 경로만** 지원한다(소셜 로그인은 범위 밖, README에
  한계로 명시). "메일로 시작하기" 클릭 시 `https://accounts.yanolja.com/v3/login/email?...`로
  이동하며, 폼은 `#email`(text), `#password`(password), 제출 버튼 텍스트 "로그인하기"로
  구성된다. 제출 버튼은 접근성 트리상 `get_by_role("button", ...)`로 안정적으로 잡히지 않아
  (실제 사이트에서도 검증 필요 - 정적 fixture에서는 확실히 실패) `get_by_text(...)`를 쓴다.
- 로그인 여부 판별: `https://nol.yanolja.com/mypage` (실제로는 `/member/mypage`로 리다이렉트)
  접속 후 본문에 **"로그인 또는 회원가입하기"** 문구가 있으면 비로그인 상태다. 로그인 성공
  후 이 문구가 사라지는지는 실제 계정으로 미검증(Task #3 완료 조건).
- 예약내역: 마이페이지의 "NOL 예약 내역" 섹션 아래 "국내숙소/레저/교통" 등 카테고리별
  하위 항목이 실제 진입점이다(섹션 제목 자체는 클릭 불가). 비로그인 상태에서 "국내숙소/레저/
  교통"을 클릭하면 `https://nol.yanolja.com/visitorlogin?type=platformOrder`(비회원 예약조회)
  로 이동한다 - 이는 로그인 상태에서의 목적지와 다를 것으로 추정되지만, **실제 로그인 후
  이동하는 목적지와 그 페이지의 예약 카드 마크업은 아직 관찰하지 못했다**(자격증명 없이는
  확인 불가). 2단계 전 "(구) NOL 인터파크 예약 내역"이라는 별도 섹션도 있어, 구 인터파크
  계정으로 예약한 내역은 별도 페이지일 수 있다 - 이번 구현 범위에서는 "NOL 예약 내역 > 국내
  숙소/레저/교통"만 다루고 인터파크 레거시 예약은 비범위로 둔다(README에 명시).
- **구현 상태**: `login()`/`is_logged_in()`은 위 실측 내용대로 구현했다. `fetch_reservations()`
  는 로그인 여부 재확인 + 예약내역 진입 클릭까지만 구현하고, 그 뒤 실제 예약 카드 파싱은
  `ScrapeStructureError`를 던지는 TODO로 남겨뒀다(`sites/yanolja.py` 모듈 docstring 참고) -
  실제 YANOLJA_ID/YANOLJA_PW로 로그인 후 관찰해야 완성할 수 있다.
- 리스크: Next.js SPA라 `networkidle` 대기가 느리거나 불안정할 수 있음 - 지금까지는 동작했지만
  실제 로그인 플로우(리다이렉트가 많음)에서 타임아웃이 나면 `page.wait_for_selector` 기반
  대기로 전환을 검토한다.

### 8.2 여기어때 (goodchoice.kr → www.yeogi.com) — Task #4에서 실측 완료 (로그인만; 예약내역 진입점은 미발견)
- `goodchoice.kr`은 `www.yeogi.com`으로 리다이렉트된다(실제 서비스 도메인). 인증은 별도
  서브도메인 `platform.yeogi.com`에서 처리한다.
- 로그인: 홈(`www.yeogi.com`)에서 "로그인" 클릭 →
  `https://platform.yeogi.com/login?redirectUri=https://www.yeogi.com/`. 카카오/네이버/
  Apple/페이스북/구글 소셜 로그인 버튼과 "이메일로 시작하기" 버튼이 있고, 이 라이브러리는
  **"이메일로 시작하기"(ID/PW) 경로만** 지원한다(8.1과 동일하게 소셜 로그인은 범위 밖).
  클릭 시 `.../login/email`로 이동, 폼은 `#userEmail`(type=email), `#userPassword`
  (password), 제출 버튼 텍스트 **"로그인"**(단, 네비게이션 바의 "로그인/회원가입"과 겹치므로
  `exact=True`로 구분해야 함 - `get_by_text(..., exact=False)`를 쓰면 엉뚱한 버튼을 클릭할
  위험이 있다).
- 로그인 여부 판별: 로그인 후 `redirectUri`로 지정한 `www.yeogi.com` 홈으로 돌아오므로, 홈
  본문에 **"로그인/회원가입"** 문구가 있으면 비로그인 상태로 판단한다(실제 로그인 성공 후
  이 문구가 사라지는지는 미검증 - Task #4 완료 조건).
- 예약내역: **진입점을 찾지 못했다.** 비로그인 상태의 홈 메뉴에는 "비회원 예약조회"
  (`platform.yeogi.com/guest/reservation/check`)만 있고, 회원용 예약내역 링크는 로그인 후에만
  nav에 나타나는 것으로 보인다(로그아웃 상태에서는 어떤 정적 href/클릭 가능 텍스트로도
  발견되지 않음). `platform.yeogi.com/reservation`, `/member/reservation`, `/mypage` 등 추정
  경로는 모두 `ERR_HTTP_RESPONSE_CODE_FAILURE`로 실패했다(인증 없이 접근 불가능한 것으로
  추정). **실제 로그인 후 nav를 직접 관찰해야만** 알 수 있다.
- **구현 상태**: `login()`/`is_logged_in()`은 위 실측대로 구현했다. `fetch_reservations()`는
  로그인 상태 확인까지만 하고, 예약내역 진입점을 못 찾았으므로 즉시 `ScrapeStructureError`를
  던진다(`sites/yeogiotae.py` 모듈 docstring 참고) - 실제 YEOGIOTAE_ID/YEOGIOTAE_PW로 로그인
  후 nav를 관찰해야 완성할 수 있다. 야놀자보다 더 이른 단계에서 막혀 있다.
- **테스트 인프라 주의사항**: 여기어때(Nuxt/Vue)는 번들 스크립트를 parser-blocking
  `document.write()`로 주입한다. 네트워크 없이 `page.set_content(fixture_html)`로 재생하면
  그 스크립트 로드가 실패하는 시점에서 HTML 파싱 자체가 멈춰, 소스에는 실제로 존재하는
  `<input>` 태그들이 DOM에는 전혀 나타나지 않는다(반면 그 이전에 나온 텍스트는 `inner_text`로
  정상 추출됨). 그래서 `tests/unit/test_yeogiotae.py`의 로그인폼 필드 검증은 Playwright
  locator가 아니라 저장된 HTML 원문에 대한 문자열 포함 검사로 작성했다 - 야놀자(Next.js)
  fixture에서는 이 문제가 없었다(사이트별로 fixture 테스트 방식이 다를 수 있음을 염두에
  둔다).

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
- 네이버 로그인은 아이디 입력 화면과 비밀번호 입력 화면이 분리된 2-step으로 알려져 있다.
  `NaverClient.login()`은 7절에서 정의한 `_enter_identifier()` / `_enter_password()` 훅을
  각각 구현하고, 그 사이의 화면 전환을 `page.wait_for_selector`로 기다린다(단일 폼으로
  가정하지 않음).

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
  반환하되, 에이전트가 텍스트 파싱 없이 분기할 수 있도록 **구조화된 필드도 함께** 담는다:
  `{"error_type": "TwoFactorRequiredError", "message": "...", "site": "naver"}` 형태를
  structured content로 반환한다. 특히 `TwoFactorRequiredError`는 "사람이
  `tripsite login <site>`를 headed 모드로 직접 실행해야 함"을 `message`에 명시한다.
- **운영 전제**: MCP 서버와 `cli.py`는 **동일 머신, 동일 `TRIPSITE_STATE_DIR`**를 공유하는
  것을 전제로 한다(10절). MCP 서버가 원격/헤드리스 컨테이너에서 돌고 CLI는 로컬 GUI에서
  도는 식으로 상태 디렉터리가 분리되면 "사람이 CLI로 로그인"이라는 복구 경로 자체가
  성립하지 않는다 — 이 경우의 대안은 로컬에서 1회 `tripsite login <site>`를 실행해 만든
  `storage_state_<site>.json`을 원격 `TRIPSITE_STATE_DIR`로 복사하는 것이며, README에
  이 운영 전제와 대안을 명시한다.
- **사전 요구사항**: Playwright는 별도 브라우저 바이너리 설치(`playwright install
  chromium`)가 필요하다. 이는 `pip install`만으로는 끌려오지 않으므로, `mcp_server.py`
  기동 시 브라우저 바이너리 존재 여부를 확인하고, 없으면 "`playwright install chromium`을
  먼저 실행하세요"라는 명확한 에러로 즉시 종료한다(README에도 설치 단계로 명시).
- 조회 호출에는 5절의 `TRIPSITE_MIN_QUERY_INTERVAL_SEC` 캐시가 적용되어, 같은 사이트를
  그 간격 내에 다시 조회하면 마지막 결과를 그대로 반환한다(불필요한 재조회로 인한 차단
  리스크 감소).
- 서버는 비밀번호/쿠키 값을 tool 응답이나 로그에 절대 포함하지 않는다.

### 트랜스포트
- 기본은 stdio (Claude Desktop/Code의 로컬 MCP 서버 등록 방식과 호환).
- 추후 필요 시 SSE/streamable-http 추가는 범위 밖(필요해지면 별도 설계).

## 10. CLI (`cli.py`)

- `tripsite login <site>`: headed 브라우저를 띄우고 `client.login()`을 호출한다(ID/PW는
  자동 주입). `login()`이 `TwoFactorRequiredError`를 던지면 CLI가 그 예외를 **잡아서**
  "2FA/캡차를 직접 처리한 뒤 Enter를 누르세요"를 출력하고 Enter 입력을 기다렸다가
  `is_logged_in()`을 재확인하는 루프를 돈다(7절과 통일: `login()` 자체는 블로킹 대기를
  하지 않고 항상 예외로 신호를 보내며, 대기/재확인 루프는 오직 CLI의 책임이다). 로그인이
  확인되면 `storage_state`를 저장한다.
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
- **선택자 드리프트 선제 탐지**: fixture 기반 unit 테스트는 사이트 구조가 실제로 바뀌었는지
  감지하지 못한다(고정된 샘플만 재검증). 따라서 PR 게이트용 1회성 live e2e와 별개로, 운영
  단계에서는 주 1회 정도 수동(또는 사용자가 원할 경우 별도 스케줄) live e2e 실행을 권장해
  선택자 깨짐을 사용자가 MCP 호출에서 맞닥뜨리기 전에 먼저 알아챌 수 있게 한다. 이 스케줄의
  운영 주체(사용자 본인 vs CI)는 14절의 열린 질문으로 남긴다.

## 12. 보안 체크리스트

- [ ] `.gitignore`에 `.env`, `*.secret.json`, `~/.tripsite-api` 상당 경로, `debug/` 포함
- [ ] 비밀번호는 어떤 예외 메시지/로그에도 노출되지 않음 (`Credentials.__repr__` 마스킹)
- [ ] `storage_state` 파일은 리포 바깥 디렉터리에만 저장
- [ ] README에 "개인 계정 전용, 과도한 호출 금지, 각 사이트 ToS 확인 책임은 사용자에게 있음"
      명시
- [ ] README에 "비정상적으로 자주 로그인/조회하면 계정이 일시 제한되거나 캡차가 강화될 수
      있음"을 명시적 경고로 추가 (1절)
- [ ] 디버그 덤프(`ScrapeStructureError` 첨부 HTML/스크린샷) 저장 전 비밀번호 필드 마스킹
      처리가 실제로 호출되는지 확인 (7절)
- [ ] `TRIPSITE_STATE_DIR`를 OS 백업 도구가 평문으로 백업하지 않도록 README에 경고 추가
- [ ] `Credentials.password`가 `dataclasses.asdict()`/`vars()`/`__dict__` 경로로도 평문
      노출되지 않음 (`SecretStr` 래퍼로 검증; `repr`/`__str__` 오버라이드만으로는 불충분)
- [ ] `storage_state_<site>.json`/`login_attempts_<site>.json` 저장 시 실제로 `0600`
      권한이 적용되는지 테스트로 검증 (구현만 하고 끝나지 않도록)
- [ ] 커밋 전 `git diff`/`git status`로 실수로 추가된 state/env 파일 여부 확인

## 13. 구현 로드맵 (Task 매핑)

1. Task #2: `models.py`, `errors.py`, `config.py`, `browser.py`, `sites/base.py`,
   `registry.py`, `.gitignore`, `pyproject.toml`, CI 뼈대. 이 task의 서브태스크로 (a)
   `registry.py`의 사이트별 BrowserSession+Lock 소유권이 6절대로 동작하는지, (b) 서로 다른
   사이트의 Lock이 실제로 독립적인지, (c) 단일 Chromium 프로세스에서 여러 페이지를 동시에
   띄웠을 때 자원 경합이 수용 가능한 수준인지를 간단한 통합 테스트로 검증한다.
2. Task #3: 야놀자 어댑터 (+ 실측으로 본 문서 8.1 갱신).
3. Task #4: 여기어때 어댑터 (+ 8.2 갱신).
4. Task #5: 네이버예약 어댑터 (+ 8.3 갱신).
5. Task #6: `mcp_server.py` + `cli.py`.
6. Task #7: README 마무리.

## 14. Open Questions

### v1 리뷰에서 제기되어 본 문서(v2)에서 해결한 항목
- 2-step 로그인 처리 → 7절 `_enter_identifier`/`_enter_password` 훅으로 해결.
- `get_all_reservations` 병렬 실행과 Lock 상충 여부 → 6절에서 레지스트리가 사이트별로
  독립된 Lock을 소유함을 명시하고, Task #2(13절)에 검증 서브태스크로 할당.
- 전체 조회 상한 → 7절 `MAX_RESERVATIONS_UNBOUNDED`(기본 500)로 명시.

### 여전히 열려 있는 질문 (구현 중 실측/결정 필요)
- 세션 만료 판단을 "로그인 페이지로 리다이렉트"만으로 하는 것이 충분한가, 아니면 네이버처럼
  "정상 페이지인데 특정 배너/모달로 재인증을 요구"하는 패턴도 있는가? (사이트별 어댑터
  구현 시 실측하여 `is_logged_in()`에 반영)
- 11절의 "주 1회 선제 live e2e" 스케줄을 사용자가 직접 수동 실행할지, 아니면 (사용자 소유
  환경에서) 스케줄 트리거로 자동화할지는 README 마무리(Task #7) 단계에서 사용자에게 확인.
- `TRIPSITE_MIN_LOGIN_INTERVAL_SEC`/`TRIPSITE_MAX_LOGIN_ATTEMPTS_PER_DAY`(5절)의 기본값이
  실제 각 사이트의 차단 임계치에 비해 적절한지는 사전에 알 수 없다 — 실측 중 차단/캡차
  징후가 보이면 기본값을 더 보수적으로 낮춘다.
