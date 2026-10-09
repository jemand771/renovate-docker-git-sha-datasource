import base64
import json
import os
import subprocess

import httpx
import pytest
from fastapi.testclient import TestClient

from renovate_docker_git_sha_datasource import git
from renovate_docker_git_sha_datasource.app import create_app

IMAGE = "registry.example/project/image"
CREDENTIALS = base64.b64encode(b"user:secret").decode()


def run_git(repo, *args):
    return subprocess.run(
        ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def commit(repo, message):
    run_git(repo, "commit", "--allow-empty", "-m", message)
    return run_git(repo, "rev-parse", "HEAD")


@pytest.fixture
def upstream(tmp_path):
    repo = tmp_path / "upstream"
    repo.mkdir()
    run_git(repo, "init", "-b", "main")
    return repo


@pytest.fixture(autouse=True)
def environment(tmp_path, monkeypatch):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"auths": {"registry.example": {"auth": CREDENTIALS}}}))
    monkeypatch.setenv("REGISTRY_AUTH_FILE", str(path))
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def registry_transport(tags):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            assert request.headers["Authorization"] == f"Basic {CREDENTIALS}"
            assert request.url.params["scope"] == "repository:project/image:pull"
            return httpx.Response(200, json={"token": "t0k3n"})
        if request.headers.get("Authorization") != "Bearer t0k3n":
            return httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": 'Bearer realm="https://registry.example/token",'
                    'service="registry.example",scope="repository:project/image:pull"',
                },
            )
        if request.url.path != "/v2/project/image/tags/list":
            return httpx.Response(404)
        # one tag per page to exercise pagination
        last = request.url.params.get("last")
        page = sorted(tags)
        start = page.index(last) + 1 if last else 0
        headers = {}
        if start + 1 < len(page):
            headers["Link"] = f'</v2/project/image/tags/list?n=1&last={page[start]}>; rel="next"'
        return httpx.Response(200, json={"tags": page[start : start + 1]}, headers=headers)

    return httpx.MockTransport(handler)


def lookup(tmp_path, upstream, tags, **params):
    app = create_app(
        httpx.Client(transport=registry_transport(tags)),
        git.RepositoryCache(tmp_path / "cache", max_age=0),
    )
    with TestClient(app) as client:
        return client.get(
            "/releases",
            params={"image": IMAGE, "repository": str(upstream), "branch": "main"} | params,
        )


def digest(response):
    assert response.status_code == 200, response.text
    releases = response.json()["releases"]
    return releases[0]["digest"] if releases else None


def test_newest_tagged_commit_per_image(tmp_path, upstream):
    a, b, c = (commit(upstream, m) for m in "abc")
    assert digest(lookup(tmp_path, upstream, {a, b, c, "latest"}, current=a)) == c
    assert digest(lookup(tmp_path, upstream, {a, b}, current=a)) == b


def test_release_shape(tmp_path, upstream):
    a = commit(upstream, "a")
    response = lookup(tmp_path, upstream, {a})
    assert response.json() == {"releases": [{"version": "main", "digest": a}]}


def test_follows_first_parent_only(tmp_path, upstream):
    base = commit(upstream, "base")
    run_git(upstream, "checkout", "-b", "feature")
    feature = commit(upstream, "feature")
    run_git(upstream, "checkout", "main")
    run_git(upstream, "merge", "--no-ff", "-m", "merge", "feature")
    assert digest(lookup(tmp_path, upstream, {base, feature})) == base


def test_never_moves_off_current_lineage(tmp_path, upstream):
    a = commit(upstream, "a")
    run_git(upstream, "checkout", "-b", "hotfix")
    hotfix = commit(upstream, "hotfix")
    run_git(upstream, "checkout", "main")
    b = commit(upstream, "b")
    assert digest(lookup(tmp_path, upstream, {a, b, hotfix}, current=hotfix)) == hotfix
    assert digest(lookup(tmp_path, upstream, {a, b}, current="0" * 40)) == "0" * 40


def test_nothing_tagged(tmp_path, upstream):
    commit(upstream, "a")
    assert digest(lookup(tmp_path, upstream, {"latest"})) is None


def test_picks_up_new_commits(tmp_path, upstream):
    cache = git.RepositoryCache(tmp_path / "cache", max_age=0)
    a = commit(upstream, "a")
    assert git.newest_tagged(cache.get(str(upstream)), "main", {a}) == a
    b = commit(upstream, "b")
    assert git.newest_tagged(cache.get(str(upstream)), "main", {a, b}) == b


def test_not_found(tmp_path, upstream):
    commit(upstream, "a")
    assert lookup(tmp_path, upstream, set(), branch="nope").status_code == 404
    assert lookup(tmp_path, upstream, set(), branch="main~1").status_code == 404
    response = lookup(tmp_path, upstream, set(), image="registry.example/project/missing")
    assert response.status_code == 404


def test_invalid_input(tmp_path, upstream):
    assert lookup(tmp_path, upstream, set(), image="project/image").status_code == 422
    assert lookup(tmp_path, upstream, set(), current="--output=x").status_code == 422
