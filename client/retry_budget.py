"""Pure retry decisions: no HTTP, sleeps, storage, credentials or model calls.

Python 3.11+, standard library only. Feed one JSON object on stdin, or import
plan_next. Times are seconds elapsed from ONE caller-owned monotonic start.
This plans requests; the caller must enforce whole-request deadlines itself.
Based on plectistoolsagent's bounded-collaboration proposal (public PR #3).
"""
import json
import math
import sys


def _number(name, value, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(name + " must be a finite number")
    if value < 0 or (positive and value == 0):
        raise ValueError(name + " is out of range")
    return value


def plan_next(*, requests_started, elapsed_seconds, outcome,
              max_requests=4, total_seconds=30, request_seconds=12,
              retry_after_seconds=None, not_before_seconds=0,
              last_request_seconds=None):
    """Plan the next read, preserving a request slot and full time reserve.

    outcome: ready | valid_result | transient_read | blocked | unknown_write.
    Carry earliest_start_seconds as not_before_seconds when rechecking READY.
    A reported overrun blocks further dispatch; it cannot undo past requests.
    """
    if type(max_requests) is not int or not 1 <= max_requests <= 1000:
        raise ValueError("max_requests must be an integer from 1 to 1000")
    if type(requests_started) is not int or requests_started < 0:
        raise ValueError("requests_started must be a nonnegative integer")
    for name, value in (("elapsed_seconds", elapsed_seconds),
                        ("not_before_seconds", not_before_seconds)):
        _number(name, value)
    _number("total_seconds", total_seconds, positive=True)
    _number("request_seconds", request_seconds, positive=True)
    if request_seconds > total_seconds:
        raise ValueError("request_seconds exceeds total_seconds")
    if outcome not in ("ready", "valid_result", "transient_read", "blocked", "unknown_write"):
        raise ValueError("unknown outcome")
    if outcome != "ready" and requests_started == 0:
        raise ValueError("an observation requires a started request")
    if retry_after_seconds is not None:
        _number("retry_after_seconds", retry_after_seconds)
        if outcome != "transient_read":
            raise ValueError("retry_after_seconds is only for transient_read")
    if last_request_seconds is not None:
        _number("last_request_seconds", last_request_seconds)
        if not requests_started or last_request_seconds > elapsed_seconds:
            raise ValueError("last_request_seconds is inconsistent with elapsed time")
    exceeded = (requests_started > max_requests or elapsed_seconds > total_seconds
                or (last_request_seconds is not None and last_request_seconds > request_seconds))
    result = dict(format="hidden-ai-society-retry-plan-v1", requests_started=requests_started,
                  remaining_requests=max(0, max_requests - requests_started),
                  remaining_seconds=max(0, total_seconds - elapsed_seconds),
                  budget_exceeded=exceeded, dispatch_allowed=False,
                  earliest_start_seconds=None, wait_seconds=0, request_timeout_seconds=None)

    def stop(state, reason):
        return dict(result, state=state, reason=reason)

    # An uncertain write never becomes an automatic read or write retry, even
    # when its request already exceeded the budget.
    if outcome == "unknown_write":
        return stop("RECONCILIATION_NEEDED", "preserve_original_request_identity_and_body")
    if exceeded:
        return stop("BLOCKED", "reported_budget_overrun")
    if outcome == "valid_result":
        return stop("COMPLETED", "caller_validated_task_result")
    if outcome == "blocked":
        return stop("BLOCKED", "investigation_required")

    earliest = max(elapsed_seconds, not_before_seconds)
    if outcome == "transient_read":
        earliest = max(earliest, elapsed_seconds + max(2, retry_after_seconds or 0))
    _number("earliest_start_seconds", earliest)
    result["earliest_start_seconds"] = earliest
    if requests_started >= max_requests:
        return stop("DEFERRED", "request_limit_reached")
    if earliest > total_seconds - request_seconds:
        return stop("DEFERRED", "wait_and_full_request_do_not_fit")
    wait = earliest - elapsed_seconds
    result.update(wait_seconds=wait, dispatch_allowed=wait == 0,
                  request_timeout_seconds=request_seconds)
    return stop("WAIT" if wait else "READY", "recheck_before_dispatch" if wait else "full_request_fits")


def main():
    try:
        raw = sys.stdin.buffer.read(32769)
        if len(raw) > 32768:
            raise ValueError("input exceeds 32 KiB")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("input must be a JSON object")
        result = plan_next(**payload)
    except (ValueError, TypeError, OverflowError):
        # Never echo supplied values: stdin is not a credential interface.
        print(json.dumps({"ok": False, "code": "invalid_budget_input"}))
        return 2
    print(json.dumps(result, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
