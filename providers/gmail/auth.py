from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ModuleNotFoundError:
    Request = None
    Credentials = None
    InstalledAppFlow = None
    build = None

SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",)
CLIENT_SECRET_ENV_VAR = "SURFACE_GMAIL_CLIENT_SECRET_FILE"


@dataclass(frozen=True)
class SetupResult:
    email_address: str | None


def ensure_google_dependencies() -> None:
    if any(module is None for module in (Request, Credentials, InstalledAppFlow, build)):
        raise RuntimeError(
            "Google API client libraries are not installed. Recreate the environment with "
            "`conda env create -f environment.yml` or install "
            "`google-api-python-client google-auth google-auth-oauthlib google-auth-httplib2`."
        )


def normalize_path(path: Path | str | None) -> Path | None:
    if path is None:
        return None
    return Path(path).expanduser().resolve()


def save_credentials(token_path: Path, credentials: Any) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_payload = json.loads(credentials.to_json())
    token_path.write_text(json.dumps(token_payload, indent=2), encoding="utf-8")


def build_gmail_service(credentials: Any) -> Any:
    ensure_google_dependencies()
    try:
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)
    except Exception as exc:
        raise RuntimeError(f"Could not initialize the Gmail API client: {exc}") from exc


def fetch_profile_email(service: Any) -> str | None:
    try:
        profile = service.users().getProfile(userId="me").execute()
    except Exception as exc:
        raise RuntimeError(f"Could not fetch the authenticated Gmail profile: {exc}") from exc
    return profile.get("emailAddress")


def resolve_client_secret_source(
    source_client_secret_path: Path | None,
    stored_client_secret_path: Path,
) -> Path:
    explicit_path = normalize_path(source_client_secret_path)
    if explicit_path is not None:
        if not explicit_path.exists():
            raise RuntimeError(f"Gmail OAuth client secrets file does not exist: {explicit_path}")
        return explicit_path

    if stored_client_secret_path.exists():
        return stored_client_secret_path

    env_value = os.environ.get(CLIENT_SECRET_ENV_VAR)
    if env_value:
        env_path = Path(env_value).expanduser().resolve()
        if not env_path.exists():
            raise RuntimeError(
                f"{CLIENT_SECRET_ENV_VAR} points to a missing Gmail OAuth client secrets file: {env_path}"
            )
        return env_path

    raise RuntimeError(
        "Missing Gmail OAuth desktop client credentials. Supply `--client-secret-file`, set "
        f"`{CLIENT_SECRET_ENV_VAR}`, or place `client_secret.json` under {stored_client_secret_path.parent}."
    )


def copy_client_secret(source_path: Path, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path != destination_path:
        shutil.copyfile(source_path, destination_path)


def load_credentials(token_path: Path) -> Any:
    ensure_google_dependencies()

    token_path = token_path.expanduser().resolve()
    if not token_path.exists():
        raise RuntimeError(
            f"Gmail auth state is missing at {token_path}. Run "
            "`python surface account setup --provider gmail --account <account>` first."
        )

    try:
        credentials = Credentials.from_authorized_user_file(str(token_path), list(SCOPES))
    except Exception as exc:
        raise RuntimeError(f"Could not read Gmail token state at {token_path}: {exc}") from exc

    if credentials.valid:
        return credentials

    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except Exception as exc:
            raise RuntimeError(f"Could not refresh the Gmail access token: {exc}") from exc
        save_credentials(token_path, credentials)
        return credentials

    raise RuntimeError(
        f"Gmail token state at {token_path} is invalid or missing a refresh token. "
        f"Run `python surface account setup --provider gmail --account <account>` again."
    )


def load_gmail_service(token_path: Path) -> Any:
    credentials = load_credentials(token_path)
    return build_gmail_service(credentials)


def run_setup(args: Any) -> SetupResult:
    ensure_google_dependencies()

    token_path = normalize_path(args.token_path)
    stored_client_secret_path = normalize_path(args.client_secret_path)
    source_client_secret_path = normalize_path(getattr(args, "source_client_secret_path", None))
    if token_path is None or stored_client_secret_path is None:
        raise RuntimeError("Gmail setup requires token and client secret paths.")

    client_secret_source = resolve_client_secret_source(source_client_secret_path, stored_client_secret_path)
    copy_client_secret(client_secret_source, stored_client_secret_path)

    print()
    print("One-time Gmail OAuth bootstrap")
    print(f"Stored client credentials: {stored_client_secret_path}")
    print(f"Token path: {token_path}")
    print("1. A browser will open to Google sign-in.")
    print("2. Choose the Gmail account you want to bind to this Surface account slug.")
    print("3. Approve Gmail read-only access.")

    flow = InstalledAppFlow.from_client_secrets_file(str(stored_client_secret_path), list(SCOPES))
    try:
        credentials = flow.run_local_server(
            port=0,
            authorization_prompt_message="Open this URL in your browser: {url}",
            success_message="Surface Gmail auth complete. You may close this tab.",
            open_browser=True,
        )
    except Exception as exc:
        raise RuntimeError(f"Gmail OAuth setup failed: {exc}") from exc
    save_credentials(token_path, credentials)

    email_address = fetch_profile_email(build_gmail_service(credentials))
    print(f"Gmail OAuth complete for {email_address or 'the selected account'}.")
    return SetupResult(email_address=email_address)
