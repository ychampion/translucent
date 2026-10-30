"""Metered intervention chat calls and strict, key-free recorded replay.

The provider still owns its global spending ledger. This session additionally
reserves every request against the campaign cap, including failed attempts.
Recordings retain the complete request and response, with a digest and the
charged cost, so replay does not need a provider client or credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from translucent.budget import BudgetExceeded, SAFETY
from translucent.intervention import canonical
from translucent.intervention_errors import ControllerError, RecordingError
from translucent.providers import TOKEN_PRICES, _token_cost
from translucent.recordings import NoRecording
from translucent.runtime import PROFILES, _client, _transient


class StopCampaign(RuntimeError):
    """A clean campaign stop, preserving the reason and all previous evidence."""

    def __init__(self, status, reason):
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass
class Settings:
    profile: str = "meta"
    budget_usd: float = 1.0
    # Reasoning models exhausted the former 2048-token budget before answering.
    max_tokens: int = 6000
    retries: int = 3
    retry_base_s: float = 2.0
    step_cap: int = 120
    reasoning_effort: str | None = "medium"

    def __post_init__(self):
        if self.profile not in PROFILES:
            raise ValueError("Choose the meta or dev provider profile")
        if not math.isfinite(self.budget_usd) or self.budget_usd <= 0:
            raise ValueError("The campaign budget must be positive and finite")
        if self.max_tokens <= 0 or self.step_cap <= 0 or self.retries < 0:
            raise ValueError(
                "Token and step caps must be positive; retries cannot be negative"
            )
        if not math.isfinite(self.retry_base_s) or self.retry_base_s < 0:
            raise ValueError("Retry delay must be finite and nonnegative")


def request_for(settings, messages, tools=None):
    """Build exactly the request that the metered provider will receive."""
    provider, model = PROFILES[settings.profile]["orchestrator"]
    request = dict(model=model, messages=messages, max_tokens=settings.max_tokens)
    if tools:
        request.update(tools=tools, tool_choice="auto")
    # Both providers take reasoning_effort. Muse Wonder's default exhausted
    # 6000 tokens on a 120-row trace; medium returned an answer within that cap.
    if settings.reasoning_effort:
        request["reasoning_effort"] = settings.reasoning_effort
    # A caller can extend its history after this call. The recording must keep
    # the request as sent, rather than a reference to that mutable history.
    return json.loads(canonical(request))


def worst_case_cost(settings, messages, tools=None):
    """Use the same conservative text estimate as the metered chat client."""
    provider, model = PROFILES[settings.profile]["orchestrator"]
    request = request_for(settings, messages, tools)
    text = json.dumps(request["messages"]) + json.dumps(request.get("tools", []))
    prices = TOKEN_PRICES[provider][model]
    return (len(text) // 2 * prices[0] + settings.max_tokens * prices[2]) * SAFETY


def _plain(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except TypeError:
            return value.model_dump()
    return {k: _plain(v) for k, v in vars(value).items()}


def assistant_message(response):
    """Keep recording metadata out of a subsequent provider chat request."""
    return {
        key: response[key]
        for key in ("role", "content", "refusal", "tool_calls")
        if key in response
    }


def atomic_recording(path, payload):
    """Publish a complete private recording, never a partially written JSON file."""
    path = Path(path)
    temporary = None
    try:
        if path.exists():
            raise FileExistsError(f"Recording already exists: {path.name}")
        fd, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except (OSError, ValueError, TypeError) as error:
        raise RecordingError(f"Cannot write recording {path.name}: {error}") from error
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


class ChatSession:
    """One campaign's metered requests, with an ordered exact replay tape."""

    def __init__(self, settings, store, *, replay=False, client=None):
        self.settings = settings
        self.store = Path(store) / "chat"
        self.replay = replay
        self.client = client
        self.provider, self.model = PROFILES[settings.profile]["orchestrator"]
        self.spent_usd = 0.0
        self.records = []
        self.last_record = None
        if not replay:
            self.store.mkdir(parents=True, exist_ok=True)
            if any(self.store.glob("*.json")):
                raise ValueError(
                    "Recording directory already has chat calls; use a fresh directory"
                )

    def _balance(self):
        if self.client is None or not hasattr(self.client, "ledger"):
            return 0.0
        return self.client.ledger.balance()["committed_usd"]

    def _paid_balance(self, entry):
        """Retain the reserved charge if local budget storage fails after a call."""
        try:
            return self._balance()
        except (OSError, sqlite3.Error) as error:
            self._charge(entry)
            raise ControllerError(f"{type(error).__name__}: {error}") from error

    def _save(self, entry):
        path = self.store / f"{len(self.records) + 1:06d}.json"
        if not self.replay:
            try:
                payload = canonical(entry)
            except (ValueError, TypeError) as error:
                raise RecordingError(
                    f"Cannot serialize paid chat recording: {error}"
                ) from error
            atomic_recording(path, payload)
        self.records.append(entry)
        self.last_record = entry

    def _charge(self, entry):
        """Account for a completed attempt before any recording serialization or I/O."""
        self.spent_usd = round(self.spent_usd + entry["cost_usd"], 12)

    def _replay(self, request):
        path = self.store / f"{len(self.records) + 1:06d}.json"
        if not path.is_file():
            raise NoRecording(
                "No exact intervention chat recording; no provider called"
            )
        try:
            entry = json.loads(path.read_text())
        except (OSError, ValueError) as error:
            raise NoRecording(
                f"Cannot read intervention chat recording: {error}"
            ) from error
        digest = hashlib.sha256(canonical(request)).hexdigest()
        if entry.get("request_sha256") != digest or entry.get("request") != request:
            raise NoRecording("Intervention chat request does not match its recording")
        if (
            entry.get("digest")
            != hashlib.sha256(
                canonical({k: v for k, v in entry.items() if k != "digest"})
            ).hexdigest()
        ):
            raise NoRecording("Intervention chat recording digest does not match")
        cost = entry.get("cost_usd")
        if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
            raise NoRecording("Intervention chat recording has an invalid cost")
        self._charge(entry)
        self._save(entry)
        return entry

    def _live(self, request, worst):
        if self.client is None:
            self.client = _client(self.provider)
        before = self._balance()
        entry = dict(
            kind="chat",
            request=request,
            request_sha256=hashlib.sha256(canonical(request)).hexdigest(),
            provider=self.provider,
            model=self.model,
            response=None,
            raw_response=None,
            usage=None,
            cost_usd=worst,
            error=None,
            recorded_at=time.time(),
        )
        try:
            reply = self.client.chat(**request)
        except BudgetExceeded as error:
            entry["cost_usd"] = max(self._paid_balance(entry) - before, 0.0)
            entry["error"] = dict(
                type=type(error).__name__, message=str(error), budget=True
            )
        except (OSError, sqlite3.Error) as error:
            # Provider SDK transport failures use their typed API exceptions.
            # Local I/O or SQLite failures are controller failures, not refusals.
            self._charge(entry)
            raise ControllerError(f"{type(error).__name__}: {error}") from error
        except Exception as error:
            code = getattr(error, "status_code", None)
            if code is None:
                code = getattr(getattr(error, "response", None), "status_code", None)
            rejected = isinstance(code, int) and 400 <= code < 500 and code != 429
            transient = not rejected and _transient(error)
            entry["cost_usd"] = max(
                0.0 if rejected else worst, self._paid_balance(entry) - before
            )
            entry["error"] = dict(
                type=type(error).__name__,
                message=str(error)[:1000],
                transient=transient,
                provider_rejected=rejected,
                status_code=code,
            )
        else:
            usage = getattr(reply, "usage", None)
            try:
                if usage is not None:
                    actual = _token_cost(TOKEN_PRICES[self.provider][self.model], usage)
                    if not math.isfinite(actual) or actual < 0:
                        raise ValueError("Provider usage gives an invalid cost")
                    # Charge the call's own tokens. The shared provider balance also moves
                    # with other campaigns running at the same time, so its change is not
                    # this call's cost: two parallel runs each counted the other's spend.
                    # The read still runs so a budget-storage failure stops the campaign.
                    self._paid_balance(entry)
                    entry["cost_usd"] = actual
            except ControllerError:
                raise
            except Exception as error:
                # The call has happened even if its usage cannot be normalized.
                # Keep the reserved ceiling charged when the actual bill is unknown.
                self._charge(entry)
                raise RecordingError(
                    f"Cannot record paid provider usage: {error}"
                ) from error
            self._charge(entry)
            try:
                return self._record_reply(entry, reply, usage)
            except RecordingError:
                raise
            except Exception as error:
                raise RecordingError(
                    f"Cannot record paid provider response: {error}"
                ) from error
        self._charge(entry)
        return self._record_entry(entry)

    def _record_reply(self, entry, reply, usage):
        """Normalize a paid response after its charge is already accounted for."""
        message = _plain(reply.choices[0].message)
        entry["response"] = {
            k: message[k]
            for k in ("role", "content", "refusal", "tool_calls")
            if k in message and message[k] is not None
        }
        entry["response"].setdefault("role", "assistant")
        entry["raw_response"] = _plain(reply)
        entry["model"] = getattr(reply, "model", None) or self.model
        entry["usage"] = _plain(usage)
        entry["finish_reason"] = getattr(reply.choices[0], "finish_reason", None)
        details = getattr(usage, "completion_tokens_details", None)
        entry["reasoning_tokens"] = getattr(details, "reasoning_tokens", None) or 0
        entry["response"].update(
            finish_reason=entry["finish_reason"],
            reasoning_tokens=entry["reasoning_tokens"],
        )
        return self._record_entry(entry)

    def _record_entry(self, entry):
        try:
            entry["digest"] = hashlib.sha256(canonical(entry)).hexdigest()
        except (ValueError, TypeError) as error:
            raise RecordingError(
                f"Cannot serialize paid chat recording: {error}"
            ) from error
        self._save(entry)
        return entry

    def chat(self, messages, tools=None):
        """Run one logical call, recording each separately metered retry."""
        request = request_for(self.settings, messages, tools)
        worst = worst_case_cost(self.settings, messages, tools)
        for attempt in range(self.settings.retries + 1):
            if self.spent_usd + worst > self.settings.budget_usd + 1e-12:
                raise StopCampaign(
                    "budget",
                    f"Next request reserves ${worst:.6f}; campaign has "
                    f"${self.settings.budget_usd - self.spent_usd:.6f} remaining",
                )
            entry = self._replay(request) if self.replay else self._live(request, worst)
            error = entry["error"]
            if error:
                if error.get("budget"):
                    raise StopCampaign("budget", error["message"])
                if error.get("transient") and attempt < self.settings.retries:
                    if not self.replay:
                        time.sleep(self.settings.retry_base_s * 2**attempt)
                    continue
                raise StopCampaign(
                    "provider_rejected"
                    if error.get("provider_rejected")
                    else "provider_error",
                    (
                        f"HTTP {error['status_code']}: "
                        if error.get("provider_rejected")
                        else ""
                    )
                    + f"{error['type']}: {error['message']}",
                )
            response = entry["response"]
            if response.get("refusal"):
                raise StopCampaign("refused", response["refusal"])
            if entry.get("finish_reason") == "length":
                raise StopCampaign(
                    "length_limit",
                    "Provider reached its length limit; "
                    f"reasoning_tokens={entry.get('reasoning_tokens', 0)}",
                )
            if entry.get("finish_reason") == "content_filter":
                raise StopCampaign(
                    "content_filter",
                    "Provider stopped the response with content_filter",
                )
            return response
        raise AssertionError("Retry loop must return or stop")

    def assert_consumed(self):
        """Refuse a replay that stopped before the recorded request sequence ended."""
        if self.replay and len(list(self.store.glob("*.json"))) != len(self.records):
            raise NoRecording(
                "Replay ended before consuming every intervention chat recording"
            )
