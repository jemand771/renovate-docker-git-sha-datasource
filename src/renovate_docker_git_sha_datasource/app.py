import logging
import os
import subprocess
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import FastAPI, HTTPException, Query

from renovate_docker_git_sha_datasource import git, registry

logger = logging.getLogger(__name__)

COMMIT = r"^[0-9a-f]{40}([0-9a-f]{24})?$"


def create_app(
    client: httpx.Client | None = None,
    cache: git.RepositoryCache | None = None,
) -> FastAPI:
    client = client or httpx.Client()
    cache = cache or git.RepositoryCache(
        Path(os.environ.get("CACHE_DIR", Path(tempfile.gettempdir(), "git-sha-datasource"))),
        float(os.environ.get("GIT_FETCH_INTERVAL", "60")),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        yield
        client.close()

    app = FastAPI(lifespan=lifespan)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/releases")
    def releases(
        image: str,
        repository: str,
        branch: str,
        current: Annotated[str | None, Query(pattern=COMMIT)] = None,
    ) -> dict[str, list[dict[str, str]]]:
        try:
            registry.split_image(image)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        try:
            tags = registry.list_tags(client, image)
            repo = cache.get(repository)
            newest = git.newest_tagged(repo, branch, tags)
        except (registry.ImageNotFoundError, git.BranchNotFoundError) as e:
            raise HTTPException(404, f"not found: {e}") from e
        except subprocess.CalledProcessError as e:
            logger.exception("git failed: %s", e.stderr)
            raise HTTPException(502, "git failed") from e
        except httpx.HTTPError as e:
            logger.exception("registry request failed")
            raise HTTPException(502, "registry request failed") from e
        if current and (newest is None or not git.is_ancestor(repo, current, newest)):
            newest = current
        if newest is None:
            return {"releases": []}
        return {"releases": [{"version": branch, "digest": newest}]}

    return app
