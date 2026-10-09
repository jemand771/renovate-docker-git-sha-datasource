import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path


class BranchNotFoundError(Exception):
    pass


def _git(*args: str | Path, offline: bool = False) -> str:
    env = os.environ | {"GIT_TERMINAL_PROMPT": "0"}
    if offline:
        # all commits are fetched, so a missing one is unknown upstream too; never fetch lazily
        env["GIT_NO_LAZY_FETCH"] = "1"
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout


class RepositoryCache:
    def __init__(self, root: Path, max_age: float) -> None:
        self._root = root
        self._max_age = max_age
        self._lock = threading.Lock()
        self._repo_locks: dict[str, threading.Lock] = {}
        self._fetched: dict[str, float] = {}

    def get(self, url: str) -> Path:
        path = self._root / hashlib.sha256(url.encode()).hexdigest()
        with self._lock:
            repo_lock = self._repo_locks.setdefault(url, threading.Lock())
        with repo_lock:
            fetched = self._fetched.get(url)
            if not path.exists():
                self._root.mkdir(parents=True, exist_ok=True)
                tmp = Path(tempfile.mkdtemp(dir=self._root))
                try:
                    _git("clone", "--bare", "--filter=tree:0", "--", url, tmp)
                except BaseException:
                    shutil.rmtree(tmp)
                    raise
                tmp.rename(path)
            elif fetched is None or time.monotonic() - fetched > self._max_age:
                _git("-C", path, "fetch", "--prune", "origin", "+refs/heads/*:refs/heads/*")
            else:
                return path
            self._fetched[url] = time.monotonic()
        return path


def newest_tagged(repo: Path, branch: str, tags: set[str]) -> str | None:
    try:
        head = _git(
            "-C", repo, "show-ref", "--verify", "--hash", f"refs/heads/{branch}", offline=True
        )
    except subprocess.CalledProcessError as e:
        raise BranchNotFoundError(branch) from e
    history = _git("-C", repo, "rev-list", "--first-parent", head.strip(), "--", offline=True)
    return next((commit for commit in history.split() if commit in tags), None)


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    try:
        _git("-C", repo, "merge-base", "--is-ancestor", ancestor, descendant, offline=True)
    except subprocess.CalledProcessError:
        return False
    return True
