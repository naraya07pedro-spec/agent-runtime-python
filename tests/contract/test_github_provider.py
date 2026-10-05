"""Synthetic GitHub REST responses; these are not captured live provider payloads."""

import json
from uuid import uuid4

import httpx
import pytest

from app.domain import Dispatch, ExternalFault
from app.github_provider import GitHubIssues

pytestmark = pytest.mark.contract


@pytest.fixture
def github_settings(settings):
    settings.github_token = __import__("pydantic").SecretStr("synthetic-github-token")
    settings.github_actor = "synthetic-bot"
    settings.github_repository = "synthetic-owner/synthetic-repo"
    return settings


def dispatch():
    return Dispatch(
        uuid4(),
        "create_issue",
        {"title": "Synthetic contract", "body": "Synthetic fixture"},
        "a" * 64,
        "b" * 64,
        1,
    )


def issue(provider, call, number=7):
    return {
        "id": 1000 + number,
        "number": number,
        "title": call.arguments["title"],
        "body": provider.body(call),
        "user": {"login": "synthetic-bot"},
    }


async def test_native_creation_contract_persists_only_normalized_response_and_request_id(
    github_settings,
):
    call = dispatch()
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url == "https://api.github.com/repos/synthetic-owner/synthetic-repo/issues"
        assert request.method == "POST"
        assert "idempotency-key" not in request.headers
        assert request.headers["x-github-api-version"] == "2022-11-28"
        payload = json.loads(request.content)
        assert payload == {"title": call.arguments["title"], "body": provider.body(call)}
        return httpx.Response(
            201,
            json={**issue(provider, call), "private_extra": "must-not-persist"},
            headers={"X-GitHub-Request-Id": "SYNTHETIC:123"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = GitHubIssues(client, github_settings)
        result = await provider.execute(call)
    assert result.external_id == "synthetic-owner/synthetic-repo#7"
    assert result.provider_request_id == "SYNTHETIC:123"
    assert "must-not-persist" not in result.model_dump_json()
    assert len(requests) == 1


@pytest.mark.parametrize(
    "failure", ["transport", "malformed", "oversized", "redirect", "rate-limit", "server-error"]
)
async def test_creation_failure_never_reissues_post(github_settings, failure):
    calls = []

    def handle(request):
        calls.append(request.method)
        if failure == "transport":
            raise httpx.ReadTimeout("synthetic-after-commit", request=request)
        if failure == "malformed":
            return httpx.Response(201, content=b'{"number":null}')
        if failure == "oversized":
            return httpx.Response(201, content=b"x" * 65537)
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://example.invalid/private"})
        return httpx.Response(429 if failure == "rate-limit" else 503, headers={"Retry-After": "1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = GitHubIssues(client, github_settings)
        with pytest.raises(ExternalFault):
            await provider.execute(dispatch())
    assert calls == ["POST"]


@pytest.mark.parametrize(
    "mutation", ["actor", "body", "title", "pull_request", "duplicate", "missing"]
)
async def test_lookup_requires_one_matching_trusted_issue_and_never_authorizes_absence(
    github_settings, mutation
):
    call = dispatch()
    methods = []

    def handle(request):
        methods.append(request.method)
        data = issue(provider, call)
        if mutation == "actor":
            data["user"] = {"login": "untrusted-user"}
        if mutation == "body":
            data["body"] = "edited body"
        if mutation == "title":
            data["title"] = "edited title"
        if mutation == "pull_request":
            data["pull_request"] = {}
        items = (
            []
            if mutation == "missing"
            else [data, issue(provider, call, 8)]
            if mutation == "duplicate"
            else [data]
        )
        return httpx.Response(200, json=items)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = GitHubIssues(client, github_settings)
        if mutation in {"actor", "title", "pull_request"}:
            with pytest.raises(ExternalFault, match="github_identity_mismatch"):
                await provider.lookup(call)
        else:
            assert (await provider.lookup(call)).status == "unknown"
    assert methods == ["GET"]


async def test_lookup_after_commit_timeout_uses_only_get_and_finds_same_identity(github_settings):
    call = dispatch()
    committed, methods = [], []

    def handle(request):
        methods.append(request.method)
        if request.method == "POST":
            committed.append(issue(provider, call))
            raise httpx.ReadTimeout("synthetic timeout after provider commit", request=request)
        return httpx.Response(200, json=committed)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = GitHubIssues(client, github_settings)
        with pytest.raises(ExternalFault):
            await provider.execute(call)
        recovered = await provider.lookup(call)
    assert recovered.status == "found"
    assert recovered.result.operation_key == call.operation_key
    assert methods == ["POST", "GET"]


async def test_lookup_is_bounded_and_does_not_follow_supplied_next_url(github_settings):
    methods = []

    def handle(request):
        methods.append(str(request.url))
        return httpx.Response(
            200,
            json=[{"body": "unrelated synthetic item"}] * 10,
            headers={"Link": '<https://example.invalid/steal>; rel="next"'},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = GitHubIssues(client, github_settings)
        assert (await provider.lookup(dispatch())).status == "unknown"
    assert len(methods) == 3
    assert all(
        url.startswith("https://api.github.com/repos/synthetic-owner/synthetic-repo/")
        for url in methods
    )


async def test_missing_configuration_fails_before_http_and_request_id_is_validated(settings):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("unexpected network I/O"))
    ) as client:
        provider = GitHubIssues(client, settings)
        with pytest.raises(ExternalFault, match="github_configuration_missing"):
            await provider.execute(dispatch())
