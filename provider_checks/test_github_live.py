"""Optional, explicitly authorized single synthetic issue; excluded from default tests/.

Credentials must be provisioned outside chat. No existing connected-account token
is extracted. There is no automatic POST retry or cleanup write on uncertain failure.
"""

import os
from uuid import uuid4

import httpx
import pytest

from app.config import Settings
from app.domain import Dispatch, fingerprint
from app.github_provider import GitHubIssues


async def test_optional_single_live_issue_contract():
    if (
        os.environ.get("RUNTIME_RUN_LIVE_PROVIDER") != "1"
        or os.environ.get("RUNTIME_LIVE_PROVIDER_WRITE_ACK") != "disposable-repository"
    ):
        pytest.skip("live provider writes require explicit disposable-repository opt-in")
    names = ("RUNTIME_GITHUB_TOKEN", "RUNTIME_GITHUB_REPOSITORY", "RUNTIME_GITHUB_ACTOR")
    if not all(os.environ.get(name) for name in names):
        pytest.skip("dedicated provider credentials/configuration unavailable")
    settings = Settings(
        _env_file=None,
        github_token=os.environ[names[0]],
        github_repository=os.environ[names[1]],
        github_actor=os.environ[names[2]],
    )
    identity = uuid4().hex
    call = Dispatch(
        uuid4(),
        "create_issue",
        {
            "title": "Synthetic agent runtime contract " + identity,
            "body": "Explicitly opted-in synthetic provider test. No client data or production traffic.",
        },
        fingerprint({"fixture": identity}),
        fingerprint({"operation": identity}),
        1,
        provider_binding=f"github:{settings.github_repository.lower()}:{settings.github_actor}",
    )
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
        provider = GitHubIssues(client, settings)
        # Exactly one POST is attempted. An uncertain result fails the test; never retry it.
        result = await provider.execute(call)
        lookup = await provider.lookup(call)
        assert lookup.status == "found"
        assert lookup.result.external_id == result.external_id
