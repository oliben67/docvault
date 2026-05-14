# Future imports (must occur at the beginning of the file):
from __future__ import annotations

# Standard library imports:
import asyncio
from datetime import UTC, datetime
from pathlib import Path

# Third party imports:
import git

from .document import CommitInfo


class GitBackend:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._repo: git.Repo | None = None

    async def init(self) -> None:
        def _do() -> git.Repo:
            if (self.path / ".git").exists():
                return git.Repo(self.path)
            return git.Repo.init(self.path)

        self._repo = await asyncio.to_thread(_do)

    @property
    def repo(self) -> git.Repo:
        if self._repo is None:
            self._repo = git.Repo(self.path)
        return self._repo

    async def commit(self, message: str, author_name: str, author_email: str) -> str:
        def _do() -> str:
            repo = self.repo
            repo.git.add("-A")
            # On initial commit HEAD doesn't exist; otherwise skip if nothing staged.
            if (
                repo.head.is_valid()
                and not repo.git.diff("--cached", "--name-only").strip()
            ):
                return repo.head.commit.hexsha[:12]
            actor = git.Actor(author_name, author_email)
            c = repo.index.commit(message, author=actor, committer=actor)
            return c.hexsha[:12]

        return await asyncio.to_thread(_do)

    async def log(
        self, path: str | None = None, max_count: int = 50
    ) -> list[CommitInfo]:
        def _do() -> list[CommitInfo]:
            if not self.repo.head.is_valid():
                return []
            kwargs: dict = {"max_count": max_count}
            if path:
                kwargs["paths"] = path
            return [
                CommitInfo(
                    sha=c.hexsha[:12],
                    message=c.message.strip(),
                    author=c.author.name,
                    timestamp=datetime.fromtimestamp(c.authored_date, tz=UTC),
                )
                for c in self.repo.iter_commits(**kwargs)
            ]

        return await asyncio.to_thread(_do)

    async def show(self, ref: str, file_path: str) -> str:
        return await asyncio.to_thread(self.repo.git.show, f"{ref}:{file_path}")

    async def create_tag(self, name: str, message: str) -> None:
        await asyncio.to_thread(self.repo.create_tag, name, message=message)

    async def list_tags(self) -> list[str]:
        def _do() -> list[str]:
            return [t.name for t in self.repo.tags]

        return await asyncio.to_thread(_do)

    async def is_empty(self) -> bool:
        return await asyncio.to_thread(lambda: not self.repo.head.is_valid())
