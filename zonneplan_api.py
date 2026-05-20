# -*- coding: utf-8 -*-

from datetime import datetime
import time
import requests

from constants import BASE_URL, APP_VERSION


class ZonneplanApi:
    def __init__(self, logger, token=None):
        self._log = logger
        self.session = requests.Session()
        self.token = None
        self.set_token(token or None)

    def _set_token(self, token_dict):
        """
        Store token dict and record when it was obtained.
        """
        if token_dict and isinstance(token_dict, dict):
            token_dict["token_obtained_at"] = time.time()
        self.token = token_dict

    def set_token(self, token):
        self._set_token(token)

    def _token_expires_soon(self, skew_seconds=120):
        """
        True if access token is missing or (almost) expired.

        Uses: token_obtained_at + expires_in (seconds).
        If expires metadata is missing, returns False (can't predict).
        """
        if not self.token:
            return True

        access = self.token.get("access_token")
        expires_in = self.token.get("expires_in")
        obtained_at = self.token.get("token_obtained_at")

        if not access:
            return True

        # If API doesn't provide expiry metadata, we can't predict -> don't force refresh
        if not expires_in or not obtained_at:
            return False

        try:
            expires_in = int(expires_in)
            obtained_at = float(obtained_at)
        except Exception:
            return False

        return (time.time() >= (obtained_at + expires_in - skew_seconds))

    def _base_headers(self):
        return {
            "content-type": "application/json;charset=utf-8",
            "x-app-version": APP_VERSION,
            "x-app-environment": "production",
        }

    def _auth_headers(self):
        h = self._base_headers()
        if self.token and self.token.get("access_token"):
            h["authorization"] = f"Bearer {self.token['access_token']}"
        return h

    def _api_post(self, path, payload, auth=False):
        headers = self._auth_headers() if auth else self._base_headers()
        url = f"{BASE_URL}{path}"
        r = self.session.post(url, json=payload, headers=headers, timeout=30)
        if not r.ok:
            self._log(f"POST {path} status={r.status_code} body={r.text}", 2)
        r.raise_for_status()
        return r.json()

    def _api_get(self, path, auth=False, retry_on_401=True):
        # Proactive refresh: refresh shortly before expiry.
        if auth and self._token_expires_soon():
            try:
                self.refresh_token()
            except Exception as e:
                # Don't hard-fail here; request may still succeed, or 401-retry below handles it.
                self._log(f"Token refresh vooraf mislukt: {e}", 2)

        headers = self._auth_headers() if auth else self._base_headers()
        url = f"{BASE_URL}{path}"
        r = self.session.get(url, headers=headers, timeout=30)

        if r.status_code == 401 and auth and retry_on_401:
            self.refresh_token()
            return self._api_get(path, auth=True, retry_on_401=False)

        if not r.ok:
            self._log(f"GET {path} status={r.status_code} body={r.text}", 2)
        r.raise_for_status()
        return r.json()

    # ---- Auth ----

    def refresh_token(self):
        if not self.token or not self.token.get("refresh_token"):
            raise RuntimeError("Geen refresh token beschikbaar")

        payload = {"grant_type": "refresh_token", "refresh_token": self.token["refresh_token"]}
        data = self._api_post("/oauth/token", payload, auth=False)

        # Some providers rotate refresh_token; if not returned, keep the old one
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
        # record obtained_at for the initial token too
        self._set_token(data)
        return data

    # ---- Account ----

    def get_user_account(self):
        """
        Haal account + alle connections op via /user-accounts/me.
        Bevat address_groups > connections met market_segment en uuid.
        """
        return self._api_get("/user-accounts/me", auth=True)

    def extract_connection_uuid(self, user_account_response, market_segment="electricity"):
        """
        Haal de UUID op voor het opgegeven market_segment ('electricity' of 'gas').
        Structuur: data > address_groups[0] > connections[n] > {uuid, market_segment}
        """
        try:
            address_groups = user_account_response["data"]["address_groups"]
            for group in address_groups:
                for conn in group.get("connections", []):
                    if conn.get("market_segment") == market_segment:
                        return conn["uuid"]
        except Exception as e:
            self._log(f"Connection UUID ophalen mislukt: {e}", 2)
        return None

    # ---- Data ----

    def get_summary(self, connection_uuid):
        return self._api_get(f"/connections/{connection_uuid}/summary", auth=True)

    def get_gas(self, connection_uuid):
        return self._api_get(f"/connections/{connection_uuid}/gas", auth=True)

    # ---- Parsing helpers ----

    def _scale(self, value):
        if value is None:
            return None
        return float(value) / 10000000.0

    def parse_current_prices_from_summary(self, summary_cache):
        """
        Returns current-hour prices from summary, INCL only.
        """
        if not isinstance(summary_cache, dict):
            return None

        try:
            items = summary_cache["data"]["price_per_hour"]
        except Exception:
            return None

        now_local = datetime.now().astimezone()
        current_hour = now_local.replace(minute=0, second=0, microsecond=0)

        for item in items:
            dt = datetime.fromisoformat(item["datetime"].replace("Z", "+00:00")).astimezone()
            dt_hour = dt.replace(minute=0, second=0, microsecond=0)
            if dt_hour == current_hour:
                return {
                    "timeslot": dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "electricity_price_incl": self._scale(item.get("electricity_price")),
                    "gas_price_incl": self._scale(item.get("gas_price")),
                }

        return None

    def parse_gas_fallback(self, gas_cache):
        """
        Fallback: use /gas meta. Return incl only.
        """
        if not isinstance(gas_cache, dict):
            return {"gas_price_incl": None}

        try:
            groups = gas_cache.get("data", {}).get("measurement_groups", [])

            gas_price_incl = None

            for group in groups:
                meta = group.get("meta", {})
                if group.get("type") == "hours":
                    live_incl = meta.get("price")
                    if live_incl is not None:
                        gas_price_incl = self._scale(live_incl)
                    break

            return {"gas_price_incl": gas_price_incl}

        except Exception as e:
            self._log(f"Gas fallback parse fout: {e}", 2)

        return {"gas_price_incl": None}

    def build_forecast_payload_from_summary(self, summary_cache, gas_cache, updated, past_hours=2, future_hours=30):
        """
        updated: string timestamp to show in widget, typically last_remote_fetch
        """
        if not isinstance(summary_cache, dict):
            return None

        try:
            items = summary_cache["data"]["price_per_hour"]
        except Exception:
            return None

        if not items:
            return None

        now_local = datetime.now().astimezone()
        current_hour = now_local.replace(minute=0, second=0, microsecond=0)

        parsed = []
        current_item = None

        for item in items:
            try:
                dt = datetime.fromisoformat(item["datetime"].replace("Z", "+00:00")).astimezone()
                dt_hour = dt.replace(minute=0, second=0, microsecond=0)

                entry = {
                    "datetime": item.get("datetime"),
                    "local_datetime": dt.strftime("%Y-%m-%d %H:%M:%S"),

                    # NEW: store raw integer too (stable for fingerprinting)
                    "price_raw": item.get("electricity_price"),
                    "price": self._scale(item.get("electricity_price")),

                    "group": item.get("tariff_group"),
                    "score": item.get("sustainability_score"),
                    "is_past": dt_hour < current_hour,
                    "is_current": dt_hour == current_hour,
                }

                parsed.append((dt_hour, entry))

                if dt_hour == current_hour:
                    current_item = entry

            except Exception as e:
                self._log(f"Forecast item parse fout: {e}", 2)

        if not parsed:
            return None

        parsed.sort(key=lambda x: x[0])

        past_entries = [entry for dt_hour, entry in parsed if dt_hour < current_hour][-past_hours:]
        future_entries = [entry for dt_hour, entry in parsed if dt_hour >= current_hour][:future_hours]
        combined_entries = past_entries + future_entries

        gas_fb = self.parse_gas_fallback(gas_cache)
        gas_now = gas_fb.get("gas_price_incl")
        electricity_now = current_item.get("price") if current_item else None

        return {
            "updated": updated or "",
            "electricity_now": electricity_now,
            "gas_now": gas_now,
            "hours": combined_entries,
        }