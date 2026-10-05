"""GitHub Issues REST boundary. A marker is lookup evidence, not provider idempotency."""

import json

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
from app.domain import Dispatch, ExternalFault, ReconcileResult, ToolResult
from app.http_boundary import bounded_request
from app.tools import HttpTools


class Author(BaseModel):
    model_config = ConfigDict(strict=True)
    login: str = Field(max_length=80)


class Issue(BaseModel):
    model_config = ConfigDict(strict=True)
    id: int = Field(gt=0)
    number: int = Field(gt=0)
    title: str = Field(max_length=200)
    body: str | None = Field(max_length=8192)
    user: Author


class GitHubIssues:
    origin = "https://api.github.com"

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self.client, self.settings = client, settings

    def repository(self, tenant_id: str) -> str:
        repository = self.settings.tenant(tenant_id).github_repository
        if not repository or not self.settings.github_token or not self.settings.github_actor:
            raise ExternalFault("github_configuration_missing")
        if any(part in {".", ".."} for part in repository.split("/")):
            raise ExternalFault("github_configuration_invalid")
        return repository

    @staticmethod
    def body(call: Dispatch) -> str:
        return (
            f"{call.arguments['body']}\n\n"
            f"<!-- agent-runtime:{call.tenant_id}:{call.operation_key}:{call.fingerprint} -->"
        )

    def result(self, raw: object, call: Dispatch, request_id: str | None) -> ToolResult:
        issue = Issue.model_validate(raw)
        if (isinstance(raw, dict) and "pull_request" in raw) or (
            issue.user.login != self.settings.github_actor
            or issue.title != call.arguments["title"]
            or issue.body != self.body(call)
        ):
            raise ExternalFault("github_identity_mismatch", ambiguous=True)
        return ToolResult(
            operation_key=call.operation_key,
            fingerprint=call.fingerprint,
            external_id=f"{self.repository(call.tenant_id)}#{issue.number}",
            value=f"issue:{issue.number}",
            provider_request_id=request_id,
        )

    def headers(self) -> dict[str, str]:
        assert self.settings.github_token is not None
        return {
            "Authorization": "Bearer " + self.settings.github_token.get_secret_value(),
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def execute(self, call: Dispatch) -> ToolResult:
        repository = self.repository(call.tenant_id)
        capture: dict[str, str] = {}
        try:
            raw = await bounded_request(
                self.client,
                "POST",
                f"{self.origin}/repos/{repository}/issues",
                headers=self.headers(),
                body={"title": call.arguments["title"], "body": self.body(call)},
                timeout_seconds=self.settings.tool_timeout,
                response_headers=capture,
            )
            return self.result(json.loads(raw), call, capture.get("request_id"))
        except httpx.TransportError as exc:
            raise ExternalFault("github_transport_uncertain", ambiguous=True) from exc
        except (ValueError, ValidationError) as exc:
            raise ExternalFault("github_response_invalid", ambiguous=True) from exc

    async def lookup(self, call: Dispatch) -> ReconcileResult:
        repository = self.repository(call.tenant_id)
        matches: list[ToolResult] = []
        try:
            # Do not use eventually-consistent search or follow provider-supplied next URLs.
            # Bounded list traversal; a missing result is always unknown, never replay permission.
            for page in range(1, 4):
                capture: dict[str, str] = {}
                raw = await bounded_request(
                    self.client,
                    "GET",
                    f"{self.origin}/repos/{repository}/issues?state=all&sort=created&direction=desc&per_page=10&page={page}",
                    headers=self.headers(),
                    timeout_seconds=self.settings.tool_timeout,
                    response_headers=capture,
                )
                items = json.loads(raw)
                if not isinstance(items, list) or len(items) > 10:
                    raise ExternalFault("github_lookup_invalid")
                for item in items:
                    if isinstance(item, dict) and item.get("body") == self.body(call):
                        matches.append(self.result(item, call, capture.get("request_id")))
                if len(items) < 10:
                    break
            if len(matches) == 1:
                return ReconcileResult(status="found", result=matches[0])
            return ReconcileResult(status="unknown")
        except httpx.TransportError as exc:
            raise ExternalFault("github_lookup_transport", transient=True) from exc
        except (ValueError, ValidationError) as exc:
            raise ExternalFault("github_lookup_invalid") from exc


class ProviderTools(HttpTools):
    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        super().__init__(client, settings)
        self.github = GitHubIssues(client, settings)

    def preflight(self, tool: str | None = None, tenant_id: str = "legacy") -> None:
        if tool == "create_issue":
            self.github.repository(tenant_id)
        else:
            super().preflight(tool, tenant_id)

    async def execute(self, call: Dispatch) -> ToolResult:
        return (
            await self.github.execute(call)
            if call.tool == "create_issue"
            else await super().execute(call)
        )

    async def lookup_for(self, call: Dispatch) -> ReconcileResult:
        return (
            await self.github.lookup(call)
            if call.tool == "create_issue"
            else await super().lookup_for(call)
        )
