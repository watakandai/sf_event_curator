"""One prompt, several providers: ask each in turn until one answers.

The ranker (rank.llm_scores_chain) falls back per batch of events. This is
the same idea for callers that send one self-contained prompt at a time -
e.g. pulling an event's date and venue out of a news article - and it reuses
the ranker's provider adapters, retries and error types rather than growing
a second copy of them.
"""
from __future__ import annotations
import os
import time
import urllib.error

from . import rank


class NoProviderAvailable(RuntimeError):
    """Every provider in the chain was unconfigured or failed this prompt."""


class FallbackLLM:
    """Calls providers in order, remembering which ones are spent.

    A provider whose key isn't set is left out up front. One that hits a
    daily quota (or is still rate-limited after retries) is dropped for the
    rest of this object's life, so later prompts don't pay for a retry cycle
    that can't succeed. `min_interval` spaces calls to the same provider,
    per provider, to stay under per-minute limits.
    """

    def __init__(
        self,
        providers: list[str],
        *,
        min_interval: dict[str, float] | None = None,
        timeout: int = 90,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        for name in providers:
            if name not in rank.PROVIDERS:
                raise ValueError(
                    f"unknown provider {name!r}; expected one of {sorted(rank.PROVIDERS)}"
                )
        self.providers = [
            name for name in providers
            if os.environ.get(rank.PROVIDERS[name][0], "").strip()
        ]
        self.unconfigured = [name for name in providers if name not in self.providers]
        self.min_interval = min_interval or {}
        self.timeout = timeout
        self.sleep = sleep
        self.clock = clock
        self.spent: set[str] = set()
        self._last_call: dict[str, float] = {}

    @property
    def available(self) -> bool:
        return any(name not in self.spent for name in self.providers)

    def complete(self, prompt: str) -> tuple[str, str]:
        """Returns (reply text, "provider:model" that gave it)."""
        errors = []
        for name in self.providers:
            if name in self.spent:
                continue
            env_var, model, call = rank.PROVIDERS[name]
            key = os.environ.get(env_var, "").strip()
            self._pace(name)
            timeout = rank.PROVIDER_TIMEOUT.get(name, self.timeout)
            try:
                reply = rank._call_with_retry(call, prompt, model, key, timeout, self.sleep)
            except (urllib.error.URLError, TimeoutError, ConnectionError,
                    ValueError, KeyError) as exc:
                if getattr(exc, "status", None) == 429:
                    self.spent.add(name)
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            return reply, f"{name}:{model}"
        if not self.providers:
            raise NoProviderAvailable(
                f"no provider configured (tried {', '.join(self.unconfigured) or 'none'})"
            )
        raise NoProviderAvailable("; ".join(errors) or "every provider is spent for this run")

    def _pace(self, name: str) -> None:
        interval = self.min_interval.get(name, 0)
        last = self._last_call.get(name)
        if interval and last is not None:
            wait = interval - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self._last_call[name] = self.clock()
