# -*- coding: utf-8 -*-

from datetime import datetime, timedelta
import time

import requests

from constants import BASE_URL, APP_VERSION, ELECTRICITY_QUARTER_HOURLY_PATH


class ZonneplanApi:
    def __init__(self, logger, token=None):
        self._log = logger
        self.session = requests.Session()
        self.token = None
        self.set_token(token or None)

    def _set_token(self, token_dict):
        """Store a token dict and record when it was obtained."""
        if token_dict and isinstance(token_dict, dict):
            token_dict["token_obtained_at"] = time.time()
        self.token = token_dict

    def set_token(self, token):
        self._set_token(token)

    def _token_expires_soon(self, skew_seconds=120):
        if not self.token:
            return True

        access = self.token.get("access_token")
        expires_in = self.token.get("expires_in")
        obtained_at = self.token.get("token_obtained_at")

        if not access:
            return True
        if not expires_in or not obtained_at:
            return False

        try:
            expires_in = int(expires_in)
            obtained_at = float(obtained_at)
        except Exception:
            return False

        return time.time() >= (obtained_at + expires_in - skew_seconds)

    def _base_headers(self):
        return {
            "content-type": "application/json;charset=utf-8",
            "x-app-version": APP_VERSION,
            "x-app-environment": "production",
        }

    def _auth_headers(self):
        headers = self._base_headers()
        if self.token and self.token.get("access_token"):
            headers["authorization"] = f"Bearer {self.token['access_token']}"
        return headers

    def _api_post(self, path, payload, auth=False):
        headers = self._auth_headers() if auth else self._base_headers()
        url = f"{BASE_URL}{path}"
        response = self.session.post(url, json=payload, headers=headers, timeout=30)
        if not response.ok:
            self._log(f"POST {path} status={response.status_code} body={response.text}", 2)
        response.raise_for_status()
        return response.json()

    def _api_get(self, path, auth=False, retry_on_401=True):
        if auth and self._token_expires_soon():
            try:
                self.refresh_token()
            except Exception as exc:
                self._log(f"Token refresh vooraf mislukt: {exc}", 2)

        headers = self._auth_headers() if auth else self._base_headers()
        url = f"{BASE_URL}{path}"
        response = self.session.get(url, headers=headers, timeout=30)

        if response.status_code == 401 and auth and retry_on_401:
            self.refresh_token()
            return self._api_get(path, auth=True, retry_on_401=False)

        if not response.ok:
            self._log(f"GET {path} status={response.status_code} body={response.text}", 2)
        response.raise_for_status()
        return response.json()

    # ---- Auth ----

    def refresh_token(self):
        if not self.token or not self.token.get("refresh_token"):
            raise RuntimeError("Geen refresh token beschikbaar")

        payload = {"grant_type": "refresh_token", "refresh_token": self.token["refresh_token"]}
        data = self._api_post("/oauth/token", payload, auth=False)
        if "refresh_token" not in data and self.token.get("refresh_token"):
            data["refresh_token"] = self.token["refresh_token"]

        self._set_token(data)
        return self.token["access_token"]

    def request_login(self, email):
        return self._api_post("/auth/request", {"email": email}, auth=False)

    def get_login_request(self, login_uuid):
        return self._api_get(f"/auth/request/{login_uuid}", auth=False)

    def exchange_one_time_password(self, email, password):
        data = self._api_post(
            "/oauth/token",
            {"grant_type": "one_time_password", "email": email, "password": password},
            auth=False,
        )
        self._set_token(data)
        return data

    # ---- Account ----

    def get_user_account(self):
        return self._api_get("/user-accounts/me", auth=True)

    def extract_connection_uuid(self, user_account_response, market_segment="electricity"):
        try:
            address_groups = user_account_response["data"]["address_groups"]
            for group in address_groups:
                for connection in group.get("connections", []):
                    if connection.get("market_segment") == market_segment:
                        return connection["uuid"]
        except Exception as exc:
            self._log(f"Connection UUID ophalen mislukt: {exc}", 2)
        return None

    # ---- Data ----

    def get_summary(self, connection_uuid):
        return self._api_get(f"/connections/{connection_uuid}/summary", auth=True)

    def get_electricity_quarter_hourly(self):
        """Fetch the public Zonneplan quarter-hour electricity price chart."""
        return self._api_get(ELECTRICITY_QUARTER_HOURLY_PATH, auth=False)

    def get_gas(self, connection_uuid):
        return self._api_get(f"/connections/{connection_uuid}/gas", auth=True)

    # ---- Parsing helpers ----

    def _scale(self, value):
        if value is None:
            return None
        return float(value) / 10000000.0

    def _parse_datetime(self, value):
        if not value:
            return None
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()

    def _quarter_items(self, electricity_cache):
        try:
            items = electricity_cache["data"]["chart"]["series"]["prices"]
        except Exception:
            return []
        return items if isinstance(items, list) else []

    def _summary_items(self, summary_cache):
        try:
            items = summary_cache["data"]["price_per_hour"]
        except Exception:
            return []
        return items if isinstance(items, list) else []

    def _parsed_quarter_entries(self, electricity_cache):
        entries = []
        for item in self._quarter_items(electricity_cache):
            if not isinstance(item, dict):
                continue
            try:
                start = self._parse_datetime(item.get("start_date"))
                end = self._parse_datetime(item.get("end_date"))
                if not start:
                    continue
                if not end or end <= start:
                    end = start + timedelta(minutes=15)

                price_incl = item.get("price_tax_included", {}) or {}
                price_excl = item.get("price_tax_excluded", {}) or {}
                score = item.get("sustainability_score", {}) or {}
                entries.append({
                    "start": start,
                    "end": end,
                    "start_raw": item.get("start_date"),
                    "end_raw": item.get("end_date"),
                    "price_raw": price_incl.get("amount"),
                    "price": self._scale(price_incl.get("amount")),
                    "sell_price_ex_tax_raw": price_excl.get("amount"),
                    "sell_price_ex_tax": self._scale(price_excl.get("amount")),
                    "group": item.get("tariff_group"),
                    "score": score.get("permille"),
                })
            except Exception as exc:
                self._log(f"Kwartierprijs parse fout: {exc}", 2)
        return sorted(entries, key=lambda entry: entry["start"])

    def _parsed_summary_entries(self, summary_cache):
        raw_entries = []
        for item in self._summary_items(summary_cache):
            if not isinstance(item, dict):
                continue
            try:
                start = self._parse_datetime(item.get("datetime"))
                if not start:
                    continue
                raw_entries.append((start, item))
            except Exception as exc:
                self._log(f"Summary prijs parse fout: {exc}", 2)

        raw_entries.sort(key=lambda pair: pair[0])
        entries = []
        for index, (start, item) in enumerate(raw_entries):
            if index + 1 < len(raw_entries):
                next_start = raw_entries[index + 1][0]
            else:
                next_start = None

            # The old summary normally contains hourly points. If it ever starts
            # carrying quarter-hour points, the next timestamp defines the slot.
            if next_start and start < next_start <= start + timedelta(hours=2):
                end = next_start
            else:
                end = start + timedelta(hours=1)

            entries.append({
                "start": start,
                "end": end,
                "start_raw": item.get("datetime"),
                "end_raw": end.isoformat(),
                "price_raw": item.get("electricity_price"),
                "price": self._scale(item.get("electricity_price")),
                "sell_price_ex_tax_raw": item.get("electricity_price_excl_tax"),
                "sell_price_ex_tax": self._scale(item.get("electricity_price_excl_tax")),
                "gas_price_incl": self._scale(item.get("gas_price")),
                "group": item.get("tariff_group"),
                "score": item.get("sustainability_score"),
            })
        return entries

    def _current_entry(self, entries, now_local=None):
        now_local = now_local or datetime.now().astimezone()
        exact = [entry for entry in entries if entry["start"] <= now_local < entry["end"]]
        if exact:
            return exact[-1]
        return None

    def parse_current_prices_from_summary(self, summary_cache):
        """Parse current prices from legacy hourly or possible sub-hourly summary data."""
        entry = self._current_entry(self._parsed_summary_entries(summary_cache))
        if not entry:
            return None
        return {
            "timeslot": entry["start"].strftime("%Y-%m-%d %H:%M:%S"),
            "slot_start": entry["start_raw"],
            "slot_end": entry["end_raw"],
            "source": "summary",
            "electricity_price_incl": entry.get("price"),
            "electricity_sell_price_ex_tax": entry.get("sell_price_ex_tax"),
            "gas_price_incl": entry.get("gas_price_incl"),
        }

    def parse_current_electricity_prices(self, electricity_cache, summary_cache=None):
        """Prefer quarter-hour prices and fall back to the authenticated summary."""
        entry = self._current_entry(self._parsed_quarter_entries(electricity_cache))
        if entry:
            return {
                "timeslot": entry["start"].strftime("%Y-%m-%d %H:%M:%S"),
                "slot_start": entry["start_raw"],
                "slot_end": entry["end_raw"],
                "source": "quarter-hourly",
                "electricity_price_incl": entry.get("price"),
                "electricity_sell_price_ex_tax": entry.get("sell_price_ex_tax"),
                "gas_price_incl": None,
            }
        return self.parse_current_prices_from_summary(summary_cache)

    def parse_current_gas_from_summary(self, summary_cache):
        entry = self._current_entry(self._parsed_summary_entries(summary_cache))
        return entry.get("gas_price_incl") if entry else None

    def parse_gas_fallback(self, gas_cache):
        if not isinstance(gas_cache, dict):
            return {"gas_price_incl": None}

        try:
            groups = gas_cache.get("data", {}).get("measurement_groups", [])
            for group in groups:
                if group.get("type") == "hours":
                    raw_price = (group.get("meta", {}) or {}).get("price")
                    return {"gas_price_incl": self._scale(raw_price)}
        except Exception as exc:
            self._log(f"Gas fallback parse fout: {exc}", 2)
        return {"gas_price_incl": None}

    def _forecast_payload_from_entries(
        self,
        entries,
        gas_now,
        updated,
        past_hours,
        future_hours,
        source,
    ):
        if not entries:
            return None

        now_local = datetime.now().astimezone()
        window_start = now_local - timedelta(hours=past_hours)
        window_end = now_local + timedelta(hours=future_hours)
        current_item = self._current_entry(entries, now_local=now_local)
        combined_entries = []

        for entry in entries:
            if entry["end"] <= window_start or entry["start"] >= window_end:
                continue

            duration_minutes = int(round((entry["end"] - entry["start"]).total_seconds() / 60.0))
            combined_entries.append({
                # Preserve the v1.1 widget keys and add explicit slot metadata.
                "datetime": entry.get("start_raw"),
                "local_datetime": entry["start"].strftime("%Y-%m-%d %H:%M:%S"),
                "end_datetime": entry.get("end_raw"),
                "local_end_datetime": entry["end"].strftime("%Y-%m-%d %H:%M:%S"),
                "interval_minutes": duration_minutes,
                "price_raw": entry.get("price_raw"),
                "price": entry.get("price"),
                "sell_price_ex_tax_raw": entry.get("sell_price_ex_tax_raw"),
                "sell_price_ex_tax": entry.get("sell_price_ex_tax"),
                "group": entry.get("group"),
                "score": entry.get("score"),
                "is_past": entry["end"] <= now_local,
                "is_current": entry["start"] <= now_local < entry["end"],
            })

        return {
            "updated": updated or "",
            "source": source,
            "interval_minutes": (
                int(round((current_item["end"] - current_item["start"]).total_seconds() / 60.0))
                if current_item else None
            ),
            "electricity_now": current_item.get("price") if current_item else None,
            "electricity_sell_now_ex_tax": (
                current_item.get("sell_price_ex_tax") if current_item else None
            ),
            "gas_now": gas_now,
            "hours": combined_entries,
        }

    def build_forecast_payload(
        self,
        electricity_cache,
        summary_cache,
        gas_cache,
        updated,
        past_hours=2,
        future_hours=30,
    ):
        gas_now = self.parse_current_gas_from_summary(summary_cache)
        if gas_now is None:
            gas_now = self.parse_gas_fallback(gas_cache).get("gas_price_incl")

        quarter_entries = self._parsed_quarter_entries(electricity_cache)
        if quarter_entries:
            return self._forecast_payload_from_entries(
                quarter_entries,
                gas_now,
                updated,
                past_hours,
                future_hours,
                source="quarter-hourly",
            )

        return self.build_forecast_payload_from_summary(
            summary_cache,
            gas_cache,
            updated,
            past_hours=past_hours,
            future_hours=future_hours,
        )

    def build_forecast_payload_from_summary(
        self,
        summary_cache,
        gas_cache,
        updated,
        past_hours=2,
        future_hours=30,
    ):
        entries = self._parsed_summary_entries(summary_cache)
        gas_now = self.parse_current_gas_from_summary(summary_cache)
        if gas_now is None:
            gas_now = self.parse_gas_fallback(gas_cache).get("gas_price_incl")
        return self._forecast_payload_from_entries(
            entries,
            gas_now,
            updated,
            past_hours,
            future_hours,
            source="summary",
        )
