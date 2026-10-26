"""Small GitHub REST/GraphQL client plus the OAuth Device Code Flow."""
from __future__ import annotations

import time
from typing import Any, Callable, Iterator

import requests

from txa.constants import GITHUB_API, USER_AGENT

DEVICE_CODE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"


class GitHubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


class GitHub:
    def __init__(self, token: str | None = None, api: str = GITHUB_API):
        self.api = api.rstrip("/")
        self.http = requests.Session()
        self.http.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        if token:
            self.http.headers["Authorization"] = f"Bearer {token}"

    def request(self, method: str, path: str, ok: tuple[int, ...] = (), **kwargs) -> requests.Response:
        url = path if path.startswith("http") else f"{self.api}/{path.lstrip('/')}"
        response = self.http.request(method, url, timeout=60, **kwargs)
        if response.status_code >= 400 and response.status_code not in ok:
            try:
                body = response.json()
                message = body.get("message", "")
                if body.get("errors"):
                    message += f" {body['errors']}"
            except ValueError:
                message = response.text[:300]
            raise GitHubError(response.status_code, message)
        return response

    def get(self, path: str, **kwargs) -> Any:
        return self.request("GET", path, **kwargs).json()

    def post(self, path: str, json: Any = None, **kwargs) -> Any:
        response = self.request("POST", path, json=json, **kwargs)
        return response.json() if response.content else {}

    def patch(self, path: str, json: Any = None) -> Any:
        return self.request("PATCH", path, json=json).json()

    def put(self, path: str, json: Any = None, **kwargs) -> Any:
        response = self.request("PUT", path, json=json, **kwargs)
        return response.json() if response.content else {}

    def delete(self, path: str, **kwargs) -> None:
        self.request("DELETE", path, **kwargs)

    def paginate(self, path: str, params: dict | None = None) -> Iterator[Any]:
        url: str | None = path
        params = {"per_page": 100, **(params or {})}
        while url:
            response = self.request("GET", url, params=params)
            yield from response.json()
            url = response.links.get("next", {}).get("url")
            params = None  # the next link already carries the query string

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        data = self.post("graphql", json={"query": query, "variables": variables or {}})
        if data.get("errors"):
            raise GitHubError(200, "; ".join(e.get("message", "") for e in data["errors"]))
        return data["data"]


def device_flow(client_id: str, scope: str, on_code: Callable[[dict], None],
                sleep: Callable[[float], None] = time.sleep) -> str:
    """Run the OAuth Device Code Flow and return an access token."""
    if not client_id:
        raise GitHubError(0, "no OAuth client id configured (set TXA_CLIENT_ID or `txacreate config set client_id ...`)")
    http = requests.Session()
    http.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT})
    response = http.post(DEVICE_CODE_URL, data={"client_id": client_id, "scope": scope}, timeout=30)
    code = response.json()
    if "device_code" not in code:
        raise GitHubError(response.status_code, code.get("error_description") or code.get("error") or response.text)
    on_code(code)
    interval = int(code.get("interval", 5))
    deadline = time.monotonic() + int(code.get("expires_in", 900))
    while time.monotonic() < deadline:
        sleep(interval)
        poll = http.post(TOKEN_URL, data={
            "client_id": client_id,
            "device_code": code["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }, timeout=30).json()
        if "access_token" in poll:
            return poll["access_token"]
        error = poll.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval = int(poll.get("interval", interval + 5))
            continue
        raise GitHubError(0, poll.get("error_description") or error or "device flow failed")
    raise GitHubError(0, "the device code expired before it was authorized")
