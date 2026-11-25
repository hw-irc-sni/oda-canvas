"""Content loaders for ontology modules: git repositories (the git CLI) and mounted volumes.

Git credentials come from a Kubernetes Secret mounted as files ``<GIT_SECRETS_DIR>/<secretRef>/username`` and
``password`` (a token). They reach git through ``GIT_ASKPASS``, never through the URL, the command line or the
logs. Only the URL schemes in ``GIT_ALLOWED_SCHEMES`` are accepted (https by default). Files from a mounted
volume must be under one of ``MOUNT_ROOTS``.
"""
import asyncio
import hashlib
import logging
import os
import stat
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from ..config import Settings
from ..errors import ApiError

log = logging.getLogger(__name__)

ASKPASS = """#!/bin/sh
case "$1" in
  Username*) cat "$TMFC060_GIT_SECRET/username" ;;
  *) cat "$TMFC060_GIT_SECRET/password" ;;
esac
"""


class LoaderError(ApiError):
    def __init__(self, message: str):
        super().__init__(400, "400", "Bad Request", message)


def _askpass_script(directory: Path) -> Path:
    path = directory / "askpass.sh"
    if not path.exists():
        path.write_text(ASKPASS)
        path.chmod(stat.S_IRWXU)
    return path


async def _git(*args: str, cwd: Path | None, env: dict, timeout: int) -> None:
    process = await asyncio.create_subprocess_exec("git", *args, cwd=cwd, env=env,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError as exc:
        process.kill()
        raise LoaderError(f"git {args[0]} timed out after {timeout} s") from exc
    if process.returncode != 0:
        raise LoaderError(f"git {args[0]} failed: {stderr.decode(errors='replace').strip()[-300:]}")


async def checkout(settings: Settings, url: str, ref: str = "main", secret_ref: str | None = None) -> Path:
    """Shallow-clone (or refresh) url@ref into the cache and return the working tree."""
    scheme = urlsplit(url).scheme
    if scheme not in settings.git_allowed_schemes:
        raise LoaderError(f"git URL scheme '{scheme}' is not allowed (allowed: {', '.join(settings.git_allowed_schemes)})")
    cache = Path(settings.git_cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    workdir = cache / hashlib.sha256(f"{url}@{ref}".encode()).hexdigest()[:16]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(cache), "GIT_TERMINAL_PROMPT": "0",
           "GIT_CONFIG_NOSYSTEM": "1"}
    if secret_ref:
        secret_dir = Path(settings.git_secrets_dir) / secret_ref
        if not (secret_dir / "password").is_file():
            raise LoaderError(f"git credentials '{secret_ref}' are not mounted")
        env.update(GIT_ASKPASS=str(_askpass_script(cache)), TMFC060_GIT_SECRET=str(secret_dir))
    timeout = settings.git_timeout
    if (workdir / ".git").is_dir():
        await _git("fetch", "--depth", "1", "origin", ref, cwd=workdir, env=env, timeout=timeout)
        await _git("reset", "--hard", "FETCH_HEAD", cwd=workdir, env=env, timeout=timeout)
    else:
        tmp = Path(tempfile.mkdtemp(dir=cache))
        await _git("init", "-q", str(tmp), cwd=None, env=env, timeout=timeout)
        await _git("remote", "add", "origin", url, cwd=tmp, env=env, timeout=timeout)
        await _git("fetch", "--depth", "1", "origin", ref, cwd=tmp, env=env, timeout=timeout)
        await _git("checkout", "-q", "FETCH_HEAD", cwd=tmp, env=env, timeout=timeout)
        tmp.rename(workdir)
    return workdir


def safe_join(root: Path, relative: str) -> Path:
    """root/relative, refusing paths that escape root."""
    path = (root / relative).resolve()
    if path != root.resolve() and root.resolve() not in path.parents:
        raise LoaderError(f"path '{relative}' escapes the repository")
    return path


def mounted_path(settings: Settings, path: str) -> Path:
    resolved = Path(path).resolve()
    for root in settings.mount_roots:
        root_path = Path(root).resolve()
        if resolved == root_path or root_path in resolved.parents:
            return resolved
    raise LoaderError(f"mount path '{path}' is not under an allowed root ({', '.join(settings.mount_roots)})")


async def read_git_file(settings: Settings, source: dict) -> bytes:
    workdir = await checkout(settings, source["url"], source.get("ref") or "main", source.get("secretRef"))
    path = safe_join(workdir, source["path"])
    if not path.is_file():
        raise LoaderError(f"'{source['path']}' not found in {source['url']}@{source.get('ref') or 'main'}")
    return path.read_bytes()


def read_mount_file(settings: Settings, source: dict) -> bytes:
    path = mounted_path(settings, source["path"])
    if not path.is_file():
        raise LoaderError(f"'{source['path']}' is not a readable file")
    return path.read_bytes()
