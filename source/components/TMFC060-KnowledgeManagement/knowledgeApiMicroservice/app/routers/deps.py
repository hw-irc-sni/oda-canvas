"""Shared request dependencies."""
from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..ontology.registry import Registry


async def session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessions() as s:
        yield s


def registry_for(request: Request, s: AsyncSession) -> Registry:
    state = request.app.state
    return Registry(s, state.graphs, state.settings, state.events)
