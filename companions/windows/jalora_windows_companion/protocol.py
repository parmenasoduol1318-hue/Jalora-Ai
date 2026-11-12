from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import httpx


class ProtocolHTTPError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class PairingLink:
    api_base: str
    request_id: str
    pairing_code: str


def _validated_api_origin(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("The Jalora server address must be an HTTPS origin.")
    if parsed.scheme != "https" and parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError("Jalora server connections must use HTTPS.")
    return f"{parsed.scheme}://{parsed.netloc}"


def parse_pairing_link(value: str) -> PairingLink:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("Paste a complete Jalora pairing link.")
    if parsed.username or parsed.password:
        raise ValueError("Pairing links cannot contain user credentials.")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Pairing links must use HTTPS except on this computer.")
    values = parse_qs(parsed.fragment, strict_parsing=True)
    request_ids = values.get("pair_request_id", [])
    codes = values.get("pair_code", [])
    if len(request_ids) != 1 or len(codes) != 1:
        raise ValueError("The pairing link is missing its one-time request code.")
    return PairingLink(
        api_base=_validated_api_origin(f"{parsed.scheme}://{parsed.netloc}"),
        request_id=request_ids[0],
        pairing_code=codes[0],
    )


class DeviceProtocolClient:
    def __init__(self, api_base: str, *, timeout: float = 35.0) -> None:
        self.api_base = _validated_api_origin(api_base)
        self._http = httpx.Client(
            base_url=self.api_base,
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=False,
        )

    def close(self) -> None:
        self._http.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str = "",
        json_body: dict | None = None,
    ) -> dict:
        response = self._http.request(
            method,
            path,
            headers={"Authorization": f"Bearer {token}"} if token else {},
            json=json_body,
        )
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not response.is_success:
            error = data.get("error", {}) if isinstance(data, dict) else {}
            details = error.get("details") if isinstance(error, dict) else None
            raise ProtocolHTTPError(
                response.status_code,
                details or f"Jalora returned HTTP {response.status_code}."
            )
        if not isinstance(data, dict):
            raise TypeError("Jalora returned an invalid protocol response.")
        return data

    def inspect_pairing(self, link: PairingLink) -> dict:
        return self._request(
            "POST",
            f"/api/devices/pair-requests/{link.request_id}/target",
            json_body={"pairing_code": link.pairing_code},
        )

    def approve_pairing(self, link: PairingLink) -> dict:
        return self._request(
            "POST",
            f"/api/devices/pair-requests/{link.request_id}/approve",
            json_body={"confirm": True, "pairing_code": link.pairing_code},
        )

    def create_session(self, device_token: str) -> dict:
        return self._request(
            "POST", "/api/devices/protocol/v1/session", token=device_token
        )

    def report_manifest(self, access_token: str, manifest: dict) -> dict:
        return self._request(
            "PUT",
            "/api/devices/protocol/v1/manifest",
            token=access_token,
            json_body=manifest,
        )

    def poll(self, access_token: str) -> dict:
        return self._request(
            "GET",
            "/api/devices/protocol/v1/commands?wait_seconds=20&client_type=windows",
            token=access_token,
        )

    def decide(self, access_token: str, command_id: str, nonce: str, approve: bool) -> dict:
        return self._request(
            "POST",
            f"/api/devices/protocol/v1/commands/{command_id}/decision",
            token=access_token,
            json_body={"nonce": nonce, "approve": approve},
        )

    def receipt(
        self,
        access_token: str,
        command_id: str,
        receipt: dict,
    ) -> dict:
        return self._request(
            "POST",
            f"/api/devices/protocol/v1/commands/{command_id}/receipt",
            token=access_token,
            json_body=receipt,
        )
