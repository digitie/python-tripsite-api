# python-tripsite-api

야놀자, 여기어때, 네이버예약에 개인 계정으로 로그인해 **본인의 예약 내역**을 Playwright로
조회하는 Python 라이브러리이자, 이를 AI 에이전트가 사용할 수 있도록 감싼 MCP 서버입니다.

> 설계 배경과 상세 아키텍처는 [`docs/DESIGN.md`](docs/DESIGN.md)를 참고하세요.

## 상태

- 공통 프레임워크, 세 사이트의 **로그인 플로우**, MCP 서버, `tripsite` CLI가 구현되어 있습니다.
- 예약 목록 **파싱은 아직 미완성**입니다. 인증된 상태의 예약 페이지 마크업을 실제 계정으로
  관찰해야 완성할 수 있어서, 지금은 각 사이트가 `ScrapeStructureError`를 반환합니다.
- 로그인 e2e는 해당 사이트 계정 환경변수가 있을 때만 실행됩니다(`tests/e2e`).

## 설치

```bash
pip install -e ".[dev]"
playwright install chromium   # 최초 1회
```

## 사용법

계정 정보는 환경변수로 줍니다. 설정하지 않은 사이트는 조회 시 건너뜁니다.

```bash
export YANOLJA_ID=... YANOLJA_PW=...
export YEOGIOTAE_ID=... YEOGIOTAE_PW=...
export NAVER_ID=... NAVER_PW=...
```

```bash
tripsite login naver        # 화면이 보이는 브라우저로 1회 로그인(2단계 인증/캡차 직접 처리)
tripsite check naver        # 저장된 세션이 아직 유효한지 확인
tripsite reservations naver --limit 10
```

### MCP 서버

```bash
tripsite serve             # stdio로 MCP 서버 실행
```

Claude Desktop/Code 등 MCP 클라이언트에 등록할 때는 실행 명령을 `tripsite serve`로 지정하고,
위 계정 환경변수를 해당 서버 설정의 `env`에 넣습니다. 노출되는 도구는 다음과 같습니다.

| 도구 | 설명 |
|---|---|
| `list_supported_sites` | 지원 사이트 목록 |
| `get_reservations(site, limit?)` | 한 사이트의 예약 조회 |
| `get_all_reservations(limit?)` | 계정이 설정된 모든 사이트 조회(사이트별 상태 포함) |
| `check_login_status(site)` | 저장된 세션 유효성 확인(로그인 시도 안 함) |

실패는 `isError: true`와 함께 `error_type`, `message`, `site` 필드를 가진 구조화된 결과로
돌아옵니다. `TwoFactorRequiredError`이면 사용자가 `tripsite login <site>`를 한 번 실행해야
합니다.

## 주의사항

- **개인 계정 전용입니다.** 각 사이트의 이용약관을 확인할 책임은 사용자에게 있습니다.
- 비정상적으로 자주 로그인하거나 조회하면 계정이 일시적으로 제한되거나 캡차가 강화될 수
  있습니다. 로그인 시도는 쿨다운과 일일 한도로 제한됩니다(`TRIPSITE_MIN_LOGIN_INTERVAL_SEC`,
  `TRIPSITE_MAX_LOGIN_ATTEMPTS_PER_DAY`).
- 세션 쿠키는 `TRIPSITE_STATE_DIR`(기본 `~/.tripsite-api/state`)에 평문으로 저장됩니다. 이
  디렉터리를 클라우드 백업 등으로 외부에 동기화하지 마세요. 저장소 안에 두지 마세요.
- 자격증명을 저장소에 커밋하지 마세요. 환경변수나 MCP 클라이언트의 비밀 설정으로만 전달하세요.
- 소셜 로그인(카카오/네이버/구글 등)과 비회원 예약 조회는 지원하지 않습니다.
