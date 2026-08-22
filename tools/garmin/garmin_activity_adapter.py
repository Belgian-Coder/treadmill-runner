"""Bounded JSON-lines adapter for the unsupported Garmin Connect consumer API.

Secrets are accepted only on stdin. Stdout contains protocol messages and is
never used for Python logging. The gateway protects returned token JSON at rest.
"""

from __future__ import annotations

import json
import logging
import sys
import io
import math
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logging.disable(logging.CRITICAL)


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def read_message() -> dict[str, Any]:
    line = sys.stdin.readline()
    if not line:
        raise RuntimeError("The controlling process closed stdin.")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError("A JSON object is required.")
    return value


def classify_error(error: Exception, *, upload_started: bool) -> dict[str, Any]:
    name = type(error).__name__
    message = str(error)
    if isinstance(error, ModuleNotFoundError):
        return {"state": "failed", "kind": "provider-unavailable", "message": "The unsupported Garmin adapter dependency is not installed."}
    if "Authentication" in name or "MFA" in name or "401" in message or "403" in message:
        return {"state": "failed", "kind": "authentication", "message": "Garmin authentication was rejected or expired."}
    if "TooManyRequests" in name or "429" in message:
        return {"state": "failed", "kind": "rate-limit", "message": "Garmin rate-limited the request."}
    if "409" in message or "duplicate" in message.lower():
        return {"state": "failed", "kind": "duplicate", "message": "Garmin reports that this activity already exists."}
    if upload_started:
        return {"state": "unknown", "kind": "transport", "message": "The Garmin response was interrupted after upload began."}
    return {"state": "failed", "kind": "provider", "message": "The unsupported Garmin provider rejected the operation."}


def interpret_import_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"state": "unknown", "kind": "response", "message": "Garmin returned an unrecognized import response."}
    detail = result.get("detailedImportResult")
    if not isinstance(detail, dict):
        detail = result
    failures = detail.get("failures")
    if isinstance(failures, list) and failures:
        encoded = json.dumps(failures, separators=(",", ":")).lower()
        if "duplicate" in encoded or "already exists" in encoded:
            return {"state": "failed", "kind": "duplicate", "message": "Garmin reports that this activity already exists."}
        return {"state": "failed", "kind": "rejected", "message": "Garmin rejected the imported activity."}
    successes = detail.get("successes")
    if not isinstance(successes, list) or not successes or not isinstance(successes[0], dict):
        # garminconnect returns this only after the import POST completed with a
        # successful HTTP response whose body did not expose a detailed item ID.
        status = str(detail.get("status", "")).strip().lower()
        if status in {"uploaded", "success", "succeeded", "completed"}:
            return {"state": "confirmed"}
        return {"state": "unknown", "kind": "response", "message": "Garmin did not provide explicit import confirmation."}
    remote_id = successes[0].get("activityId") or successes[0].get("internalId")
    if remote_id is None or not str(remote_id).strip():
        return {"state": "unknown", "kind": "response", "message": "Garmin confirmed an import item without an activity identifier."}
    return {"state": "confirmed", "remoteId": str(remote_id)}


def connect(request: dict[str, Any]) -> None:
    from garminconnect import Garmin

    email = str(request.get("email", "")).strip()
    password = str(request.get("password", ""))
    if not email or not password:
        raise ValueError("Email and password are required.")

    def prompt_mfa() -> str:
        emit({"state": "mfa-required"})
        response = read_message()
        code = str(response.get("mfaCode", "")).strip()
        if not code or len(code) > 16:
            raise ValueError("A valid MFA code is required.")
        return code

    client = Garmin(email, password, prompt_mfa=prompt_mfa, retry_attempts=0)
    client.login()
    emit(
        {
            "state": "connected",
            "accountLabel": client.display_name or email,
            "tokenStore": client.client.dumps(),
        }
    )


def upload(request: dict[str, Any]) -> None:
    from garminconnect import Garmin

    token_store = str(request.get("tokenStore", ""))
    activity_path = Path(str(request.get("activityPath", ""))).resolve()
    if len(token_store) < 128:
        raise ValueError("A token store is required.")
    if activity_path.suffix.lower() != ".fit" or not activity_path.is_file():
        raise ValueError("A readable FIT activity file is required.")
    client = Garmin(retry_attempts=0)
    client.login(token_store)
    upload_started = False
    try:
        upload_started = True
        result = client.import_activity(str(activity_path))
        disposition = interpret_import_result(result)
        if disposition["state"] == "confirmed":
            disposition["tokenStore"] = client.client.dumps()
        emit(disposition)
    except Exception as error:  # provider exception taxonomy changes between releases
        emit(classify_error(error, upload_started=upload_started))


def _login(token_store: str):
    from garminconnect import Garmin

    if len(token_store) < 128:
        raise ValueError("A token store is required.")
    client = Garmin(retry_attempts=0)
    client.login(token_store)
    return client


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def search(request: dict[str, Any]) -> None:
    from garminconnect import parse_activity_detail_metrics

    client = _login(str(request.get("tokenStore", "")))
    local_start = _utc(request.get("startedAtUtc"))
    if local_start is None:
        raise ValueError("A UTC session start is required.")
    # Garmin's private activities endpoint does not consistently accept the
    # treadmill_running type key as a server-side filter. Keep the request
    # bounded and apply the exact type/start filters below instead.
    activities = client.get_activities(0, 20)
    candidates: list[dict[str, Any]] = []
    for activity in activities if isinstance(activities, list) else []:
        if not isinstance(activity, dict):
            continue
        remote_id = activity.get("activityId")
        started = _utc(activity.get("startTimeGMT"))
        activity_type = activity.get("activityType") or {}
        type_key = activity_type.get("typeKey") if isinstance(activity_type, dict) else None
        if remote_id is None or started is None or abs((started - local_start).total_seconds()) > 600:
            continue
        if str(type_key or "").lower() != "treadmill_running":
            continue
        heart_rate_samples: list[dict[str, Any]] = []
        try:
            details = client.get_activity_details(str(remote_id), maxchart=1000, maxpoly=0)
            parsed = parse_activity_detail_metrics(details if isinstance(details, dict) else {})
        except Exception:
            parsed = []  # bounded summary HR can still corroborate when chart details are unavailable
        stride = max(1, math.ceil(len(parsed) / 200))
        for sample in parsed[::stride]:
            elapsed = sample.get("sumElapsedDuration", sample.get("sumDuration"))
            bpm = sample.get("directHeartRate", sample.get("heartRate"))
            if isinstance(elapsed, (int, float)) and isinstance(bpm, (int, float)) and 0 < bpm <= 255:
                heart_rate_samples.append({"elapsedSeconds": float(elapsed), "bpm": int(round(bpm))})
        candidates.append(
            {
                "remoteId": str(remote_id),
                "activityType": "treadmill_running",
                "startedAtUtc": started.isoformat(),
                "durationSeconds": float(activity.get("duration") or 0),
                "distanceKilometers": float(activity.get("distance") or 0) / 1000.0,
                "averageHeartRate": activity.get("averageHR"),
                "maximumHeartRate": activity.get("maxHR"),
                "heartRateSamples": heart_rate_samples,
            }
        )
        if len(candidates) >= 5:
            break
    emit({"state": "confirmed", "tokenStore": client.client.dumps(), "candidates": candidates})


def download_original(request: dict[str, Any]) -> None:
    from garminconnect import Garmin

    client = _login(str(request.get("tokenStore", "")))
    remote_id = str(request.get("remoteId", "")).strip()
    output_path = Path(str(request.get("outputPath", ""))).resolve()
    if not remote_id.isdigit() or output_path.suffix.lower() != ".fit":
        raise ValueError("A Garmin activity ID and FIT output path are required.")
    payload = client.download_activity(remote_id, Garmin.ActivityDownloadFormat.ORIGINAL)
    if not isinstance(payload, bytes) or len(payload) > 32 * 1024 * 1024:
        raise ValueError("The Garmin original activity archive is invalid or too large.")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        fits = [item for item in archive.infolist() if not item.is_dir() and Path(item.filename).suffix.lower() == ".fit"]
        if len(fits) != 1 or fits[0].file_size > 16 * 1024 * 1024:
            raise ValueError("The Garmin original archive must contain one bounded FIT file.")
        fit_bytes = archive.read(fits[0])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(fit_bytes)
    emit({"state": "confirmed", "tokenStore": client.client.dumps(), "remoteId": remote_id})


def delete(request: dict[str, Any]) -> None:
    client = _login(str(request.get("tokenStore", "")))
    remote_id = str(request.get("remoteId", "")).strip()
    if not remote_id.isdigit():
        raise ValueError("A Garmin activity ID is required.")
    started = False
    try:
        started = True
        client.delete_activity(remote_id)
        emit({"state": "confirmed", "tokenStore": client.client.dumps(), "remoteId": remote_id})
    except Exception as error:
        emit(classify_error(error, upload_started=started))


def probe() -> None:
    """Import the pinned provider without contacting Garmin or reading secrets."""
    from garminconnect import Garmin

    if Garmin is None:  # pragma: no cover - defensive import contract check
        raise RuntimeError("The Garmin adapter dependency is invalid.")
    emit({"state": "ready"})


def main() -> int:
    try:
        request = read_message()
        operation = request.get("operation")
        if operation == "probe":
            probe()
        elif operation == "connect":
            connect(request)
        elif operation == "upload":
            upload(request)
        elif operation == "search":
            search(request)
        elif operation == "download":
            download_original(request)
        elif operation == "delete":
            delete(request)
        else:
            raise ValueError("Unknown adapter operation.")
        return 0
    except Exception as error:
        emit(classify_error(error, upload_started=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
