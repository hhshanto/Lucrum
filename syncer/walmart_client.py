"""Minimal Walmart Marketplace API client (OAuth2 client-credentials flow)."""

import base64
import time
import uuid

import requests

BASE_URL = "https://marketplace.walmartapis.com/v3"
TOKEN_TTL_SECONDS = 900
TOKEN_REFRESH_MARGIN = 60
MAX_RETRIES = 5


class WalmartClient:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._access_token = None
        self._token_expires_at = 0

    def _basic_auth_header(self) -> str:
        raw = f"{self.client_id}:{self.client_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def _fetch_token(self) -> None:
        resp = requests.post(
            f"{BASE_URL}/token",
            headers={
                "Authorization": self._basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "WM_SVC.NAME": "Walmart Marketplace",
                "WM_QOS.CORRELATION_ID": str(uuid.uuid4()),
            },
            data={"grant_type": "client_credentials"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expires_at = time.time() + TOKEN_TTL_SECONDS - TOKEN_REFRESH_MARGIN

    def _get_token(self) -> str:
        if self._access_token is None or time.time() >= self._token_expires_at:
            self._fetch_token()
        return self._access_token

    def _headers(self) -> dict:
        return {
            "WM_SEC.ACCESS_TOKEN": self._get_token(),
            "WM_SVC.NAME": "Walmart Marketplace",
            "WM_QOS.CORRELATION_ID": str(uuid.uuid4()),
            "Accept": "application/json",
        }

    def _get(self, url: str, params: dict | None = None) -> dict:
        for attempt in range(MAX_RETRIES):
            resp = requests.get(url, headers=self._headers(), params=params, timeout=30)

            if resp.status_code == 401 and attempt == 0:
                self._fetch_token()
                continue

            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue

            resp.raise_for_status()
            return resp.json()

        resp.raise_for_status()
        return resp.json()

    def get_orders(self, created_start_date: str, limit: int = 100):
        """Yield every order element created on/after created_start_date (ISO date)."""
        url = f"{BASE_URL}/orders"
        params = {
            "createdStartDate": created_start_date,
            "limit": limit,
            "productInfo": "true",
            "shipNodeType": "SellerFulfilled",
        }

        while True:
            data = self._get(url, params=params)
            list_payload = data.get("list", {})
            elements = list_payload.get("elements", {}).get("order", [])
            for order in elements:
                yield order

            next_cursor = list_payload.get("meta", {}).get("nextCursor")
            if not next_cursor or "hasMoreElements=true" not in next_cursor:
                break

            url = f"{BASE_URL}/orders{next_cursor}"
            params = None

    def get_items(self, limit: int = 200):
        """Yield every item in the seller's catalog (offset-based pagination)."""
        offset = 0
        while True:
            data = self._get(f"{BASE_URL}/items", params={"limit": limit, "offset": offset})
            items = data.get("ItemResponse", [])
            for item in items:
                yield item

            offset += len(items)
            if not items or offset >= data.get("totalItems", 0):
                break

    def get_available_recon_dates(self) -> list[str]:
        """Return the list of report dates available from the recon report API."""
        data = self._get(f"{BASE_URL}/report/reconreport/availableReconFiles", params={"reportVersion": "v1"})
        for key in ("availableApReportDates", "availableReconFiles", "availableDates", "files", "dates"):
            if key in data:
                return data[key]
        return []

    def get_recon_report(self, report_date: str):
        """Yield every row of the recon (settlement) report for a given date."""
        offset = 0
        limit = 1000
        while True:
            data = self._get(
                f"{BASE_URL}/report/reconreport/reconFileJson",
                params={"reportVersion": "v1", "reportDate": report_date, "offset": offset, "limit": limit},
            )
            rows = data.get("reportData", [])
            for row in rows:
                yield row

            next_offset = data.get("nextOffset")
            if not rows or next_offset in (None, "", offset):
                break
            offset = next_offset
