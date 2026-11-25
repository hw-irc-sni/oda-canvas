"""The git (CLI, askpass token) and mount loaders."""
import os
import subprocess

import pytest

from app.config import Settings
from app.errors import ApiError
from app.ontology import loaders


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"})


@pytest.fixture
def bare_repo(tmp_path):
    """A bare repository with one Turtle file on main."""
    work, bare = tmp_path / "work", tmp_path / "repo.git"
    work.mkdir()
    git("init", "-q", "-b", "main", cwd=work)
    (work / "telecom").mkdir()
    (work / "telecom" / "a.ttl").write_text("<urn:a> <urn:b> <urn:c> .\n")
    git("add", ".", cwd=work)
    git("commit", "-q", "-m", "init", cwd=work)
    git("clone", "-q", "--bare", str(work), str(bare), cwd=tmp_path)
    return work, bare


def settings(tmp_path, **kw):
    return Settings(git_allowed_schemes=["file", "https"], git_cache_dir=str(tmp_path / "cache"),
                    git_secrets_dir=str(tmp_path / "secrets"), mount_roots=[str(tmp_path / "mnt")], **kw)


async def test_git_reads_a_file_and_refreshes(tmp_path, bare_repo):
    work, bare = bare_repo
    s = settings(tmp_path)
    source = {"kind": "git", "url": f"file://{bare}", "ref": "main", "path": "telecom/a.ttl"}
    assert b"<urn:a>" in await loaders.read_git_file(s, source)
    (work / "telecom" / "a.ttl").write_text("<urn:x> <urn:y> <urn:z> .\n")
    git("commit", "-qam", "change", cwd=work)
    git("push", "-q", str(bare), "main", cwd=work)
    assert b"<urn:x>" in await loaders.read_git_file(s, source)


async def test_git_scheme_not_allowed(tmp_path, bare_repo):
    with pytest.raises(ApiError, match="scheme 'file' is not allowed"):
        await loaders.read_git_file(Settings(git_cache_dir=str(tmp_path / "c")),
                                    {"url": f"file://{bare_repo[1]}", "path": "telecom/a.ttl"})


async def test_git_path_escape_and_missing_file(tmp_path, bare_repo):
    s, url = settings(tmp_path), f"file://{bare_repo[1]}"
    with pytest.raises(ApiError, match="escapes"):
        await loaders.read_git_file(s, {"url": url, "path": "../../etc/passwd"})
    with pytest.raises(ApiError, match="not found"):
        await loaders.read_git_file(s, {"url": url, "path": "nope.ttl"})


async def test_git_credentials_go_through_askpass(tmp_path, monkeypatch):
    """The askpass script answers git's prompts from the mounted Secret files."""
    s = settings(tmp_path)
    secret = tmp_path / "secrets" / "tfo-git"
    secret.mkdir(parents=True)
    (secret / "username").write_text("bot")
    (secret / "password").write_text("t0ken")
    calls = []

    async def fake_git(*args, cwd, env, timeout):
        calls.append(env)
        if args[0] == "checkout":
            (cwd / "f.ttl").write_text("<urn:a> <urn:b> <urn:c> .")

    monkeypatch.setattr(loaders, "_git", fake_git)
    await loaders.read_git_file(s, {"url": "https://git.example.org/x.git", "path": "f.ttl", "secretRef": "tfo-git"})
    env = calls[-1]
    assert env["TMFC060_GIT_SECRET"] == str(secret) and "t0ken" not in str(env)
    out = subprocess.run([env["GIT_ASKPASS"], "Password for 'https://git.example.org':"], capture_output=True,
                         text=True, env={"TMFC060_GIT_SECRET": str(secret)}).stdout
    user = subprocess.run([env["GIT_ASKPASS"], "Username for 'https://git.example.org':"], capture_output=True,
                          text=True, env={"TMFC060_GIT_SECRET": str(secret)}).stdout
    assert (out, user) == ("t0ken", "bot")


async def test_missing_git_secret(tmp_path):
    with pytest.raises(ApiError, match="not mounted"):
        await loaders.read_git_file(settings(tmp_path), {"url": "https://h/x.git", "path": "f", "secretRef": "nope"})


def test_mount_reads_only_under_the_roots(tmp_path):
    s = settings(tmp_path)
    (tmp_path / "mnt").mkdir()
    (tmp_path / "mnt" / "m.ttl").write_text("<urn:a> <urn:b> <urn:c> .")
    assert loaders.read_mount_file(s, {"path": str(tmp_path / "mnt" / "m.ttl")})
    with pytest.raises(ApiError, match="not under an allowed root"):
        loaders.read_mount_file(s, {"path": "/etc/hostname"})
