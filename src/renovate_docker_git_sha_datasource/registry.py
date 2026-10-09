import json
import os
import re
from collections.abc import Generator
from pathlib import Path

import httpx

_CHALLENGE_PARAM = re.compile(r'(\w+)="([^"]*)"')


class ImageNotFoundError(Exception):
    pass


def split_image(image: str) -> tuple[str, str]:
    host, _, name = image.partition("/")
    if not name or not ("." in host or ":" in host or host == "localhost"):
        msg = f"image must start with a registry host: {image}"
        raise ValueError(msg)
    return host, name


def _auth_files() -> list[Path]:
    files = []
    if auth_file := os.environ.get("REGISTRY_AUTH_FILE"):
        files.append(Path(auth_file))
    if runtime_dir := os.environ.get("XDG_RUNTIME_DIR"):
        files.append(Path(runtime_dir, "containers", "auth.json"))
    files.append(Path.home() / ".config" / "containers" / "auth.json")
    files.append(Path(os.environ.get("DOCKER_CONFIG", Path.home() / ".docker"), "config.json"))
    return files


def load_credentials(host: str) -> str | None:
    for auth_file in _auth_files():
        if not auth_file.is_file():
            continue
        entry = json.loads(auth_file.read_text()).get("auths", {}).get(host)
        if entry and "auth" in entry:
            return entry["auth"]
    return None


class RegistryAuth(httpx.Auth):
    requires_response_body = True

    def __init__(self, credentials: str | None) -> None:
        self._credentials = credentials
        self._authorization: str | None = None

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response]:
        if self._authorization:
            request.headers["Authorization"] = self._authorization
        response = yield request
        if response.status_code != httpx.codes.UNAUTHORIZED:
            return
        scheme, _, challenge = response.headers.get("WWW-Authenticate", "").partition(" ")
        if scheme.lower() == "basic" and self._credentials:
            self._authorization = f"Basic {self._credentials}"
        elif scheme.lower() == "bearer":
            params = dict(_CHALLENGE_PARAM.findall(challenge))
            token_request = httpx.Request(
                "GET",
                params.pop("realm"),
                params=params,
                headers={"Authorization": f"Basic {self._credentials}"}
                if self._credentials
                else {},
            )
            token_response = yield token_request
            token_response.raise_for_status()
            body = token_response.json()
            self._authorization = f"Bearer {body.get('token') or body['access_token']}"
        else:
            return
        request.headers["Authorization"] = self._authorization
        yield request


def list_tags(client: httpx.Client, image: str) -> set[str]:
    host, name = split_image(image)
    auth = RegistryAuth(load_credentials(host))
    url: str | None = f"https://{host}/v2/{name}/tags/list"
    tags: set[str] = set()
    while url:
        response = client.get(url, auth=auth)
        if response.status_code == httpx.codes.NOT_FOUND:
            raise ImageNotFoundError(image)
        response.raise_for_status()
        tags.update(response.json().get("tags") or [])
        next_link = response.links.get("next")
        url = str(response.url.join(next_link["url"])) if next_link else None
    return tags
