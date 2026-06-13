"""OAuth 2.0 Installed App 흐름. 최초 인증은 로컬, 런타임은 token.json 로드 + 자동 갱신."""

from __future__ import annotations

from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from ..config import OAUTH_SCOPES


class ReauthRequired(RuntimeError):
    """토큰이 없거나 refresh가 실패해 사용자 재인증이 필요한 상태."""


def load_credentials(token_path: Path) -> Credentials:
    """token.json을 읽어 유효한 ``Credentials`` 반환. 필요 시 refresh.

    raises:
        ReauthRequired: 파일이 없거나 refresh가 실패한 경우.
    """
    if not token_path.exists():
        raise ReauthRequired(f"token.json이 없다: {token_path}. --auth 로 최초 인증을 수행해라.")
    creds = Credentials.from_authorized_user_file(str(token_path), list(OAUTH_SCOPES))
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise ReauthRequired(f"refresh 토큰이 만료되었거나 거부됨: {exc}") from exc
        _save_token(creds, token_path)
        return creds
    raise ReauthRequired("token.json이 유효하지 않다. --auth 로 재인증해라.")


def run_auth_flow(credentials_path: Path, token_path: Path) -> Credentials:
    """로컬 브라우저로 OAuth 동의 → token.json 저장. 로컬 머신에서만 호출."""
    if not credentials_path.exists():
        raise FileNotFoundError(
            f"OAuth 클라이언트 비밀이 없다: {credentials_path}. "
            "Google Cloud Console에서 Desktop type 클라이언트를 만들어 다운로드 후 이 경로에 두어라."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), list(OAUTH_SCOPES))
    creds = flow.run_local_server(port=0)
    _save_token(creds, token_path)
    return creds


def _save_token(creds: Credentials, token_path: Path) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
