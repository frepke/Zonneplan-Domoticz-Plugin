#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
<plugin key="zonneplan-prices" name="Zonneplan" author="Patrick" version="1.1.0" externallink="https://github.com/frepke/Zonneplan-Domoticz-Plugin">
    <description>
        Zonneplan prijzen (stroom/gas) + login flow + forecast JSON for custom widget.
        - Remote fetch: scheduled times per day (local).
        - Actual electricity + (meestal) gas komen uit summary JSON.
        - Gas fallback endpoint alleen als summary geen gas_price bevat.
        - Forecast JSON "updated" = timestamp van laatste INHOUDELIJKE wijziging (fingerprint change),
          zodat widget en Domoticz devices exact overeenkomen.
        - Daily Gas Price device removed.
    </description>
    <params>
        <param field="Username" label="Zonneplan e-mail" width="250px" required="true" />
        <param field="Mode1" label="Add missing devices" width="120px" required="true" default="Yes">
            <options>
                <option label="Yes" value="Yes" default="true" />
                <option label="No" value="No" />
            </options>
        </param>
        <param field="Mode2" label="Fallback refresh interval (minutes)" width="200px" required="true" default="30">
            <options>
                <option label="5" value="5" />
                <option label="10" value="10" />
                <option label="15" value="15" />
                <option label="30" value="30" default="true" />
                <option label="60" value="60" />
                <option label="120" value="120" />
                <option label="720" value="720" />
            </options>
        </param>
        <param field="Mode5" label="Log level" width="120px" required="true" default="Normal">
            <options>
                <option label="Normal" value="Normal" default="true" />
                <option label="Verbose" value="Verbose" />
                <option label="Debug" value="Debug" />
            </options>
        </param>
    </params>
</plugin>
"""

import Domoticz
import hashlib
import json
import time
from datetime import datetime, timedelta

from constants import (
    DEVICE_DEFS,
    FORECAST_PAST_HOURS, FORECAST_FUTURE_HOURS,
    LOGIN_COOLDOWN_SECONDS, LOGIN_PENDING_TIMEOUT_SECONDS,

    UNIT_ELEC_INCL, UNIT_ELEC_SELL_EX_TAX, UNIT_GAS_INCL,
    UNIT_STATUS, UNIT_LOGIN, UNIT_LASTUPDATE, UNIT_FORECAST_JSON,

    DAILY_FETCH_TIMES, DAILY_FETCH_WINDOW_SECONDS, HEARTBEAT_SECONDS,
)
from storage import Storage
from zonneplan_api import ZonneplanApi


class Log:
    NORMAL = 1
    VERBOSE = 2
    DEBUG = 3
    STATUS = 4
    ERROR = 5


class Plugin:
    def __init__(self):
        self.add_devices = True
        self.refresh_interval_minutes = 30  # legacy, not used for scheduling now

        self.email = ""
        self.connection_uuid = ""

        self.storage = None
        self.api = None

        self.next_poll_login_ts = 0
        self.status_lock_seconds = 120

        # How many change lines to retain in state.json
        self.fp_change_log_limit = 20

    # ---- Domoticz lifecycle ----

    def onStart(self):
        self.email = Parameters["Username"].strip()
        self.add_devices = (Parameters["Mode1"] == "Yes")

        try:
            self.refresh_interval_minutes = int(Parameters["Mode2"])
        except Exception:
            self.refresh_interval_minutes = 30

        if Parameters["Mode5"] == "Debug":
            Domoticz.Debugging(1)
        else:
            Domoticz.Debugging(0)

        self.storage = Storage(home_folder=Parameters["HomeFolder"], logger=self.displaylog)
        self.storage.load_all()

        # UUID kan leeg gelaten worden in de Domoticz config;
        # gebruik dan de automatisch opgeslagen waarde uit state.json.
        if not self.connection_uuid:
            self.connection_uuid = self.storage.state.get("connection_uuid", "")

        self.api = ZonneplanApi(logger=self.displaylog, token=self.storage.token)

        self.displaylog("Start Zonneplan plugin", Log.STATUS)

        if not self.email:
            self._set_status("Fout: e-mail ontbreekt", lock=True)
        elif not self.connection_uuid:
            self._set_status("Nog niet aangemeld (UUID wordt automatisch opgehaald na login)")
        else:
            self._set_status("Plugin gestart")

        self._create_or_update_devices()

        Domoticz.Heartbeat(int(HEARTBEAT_SECONDS))

        # --- Startup behavior ---
        refreshed_ok = False
        if self.storage.token and self.storage.token.get("refresh_token"):
            try:
                self.api.set_token(self.storage.token)
                self.api.refresh_token()
                self._sync_token_from_api(force_save=True)
                refreshed_ok = True
                self._set_status("Authenticatie OK")
            except Exception as e:
                self.displaylog(f"Token refresh bij start mislukt: {e}", Log.VERBOSE)
                if not self._is_authenticated():
                    self._set_status("Nog niet aangemeld")

        if self._is_authenticated():
            try:
                # UUID ophalen bij startup als die nog niet bekend is (bijv. eerste keer na handmatige login)
                if not self.connection_uuid:
                    try:
                        account = self.api.get_user_account()
                        uuid = self.api.extract_connection_uuid(account, market_segment="electricity")
                        if uuid:
                            self.connection_uuid = uuid
                            self.storage.state["connection_uuid"] = uuid
                            self.storage.save_state()
                            self.displaylog(f"Startup: Connection UUID automatisch opgehaald: {uuid}", Log.STATUS)
                    except Exception as e:
                        self.displaylog(f"Startup: Connection UUID ophalen mislukt: {e}", Log.ERROR)

                self.displaylog("Startup: authenticated -> fetching remote data...", Log.STATUS)
                self._fetch_remote_data(force=True)

                # IMPORTANT: do NOT force-write forecast JSON on every startup,
                # only force when we never had a fingerprint before (first run).
                force_first = not bool(self.storage.state.get("last_cache_fp"))
                self._maybe_update_forecast_json(force=force_first)

                self.displaylog("Startup: fetch done; forecast JSON update attempted.", Log.STATUS)
                if not refreshed_ok:
                    self._set_status("Authenticatie OK")
            except Exception as e:
                self.displaylog(f"Startup fetch mislukt: {e}", Log.ERROR)

        self._update_devices_from_cache(update_forecast_json=False)

    def onStop(self):
        if self.storage:
            self.storage.save_all()
        self.displaylog("Stop Zonneplan plugin", Log.STATUS)

    def onHeartbeat(self):
        self._process_pending_login()

        if self._is_authenticated() and self._daily_fetch_due_now():
            now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
            self.displaylog(f"Daily fetch HIT at {now} -> fetching remote...", Log.STATUS)

            try:
                self._fetch_remote_data(force=True)

                changed = self._maybe_update_forecast_json(force=False)
                if changed:
                    self.displaylog("Daily fetch: cache fingerprint CHANGED -> forecast JSON updated.", Log.STATUS)
                else:
                    self.displaylog("Daily fetch: no changes (cache fingerprint same).", Log.STATUS)

            except Exception as e:
                self.displaylog(f"Daily fetch: fetch error: {e}", Log.ERROR)

        self._update_devices_from_cache(update_forecast_json=False)

    def onCommand(self, Unit, Command, Level, Hue):
        if Unit == UNIT_LOGIN:
            if Command == "On":
                self._start_login_flow()
            elif Command == "Off":
                self._cancel_pending_login()

    # ---- Logging ----

    def displaylog(self, msg, level=Log.NORMAL):
        mode = Parameters.get("Mode5", "Normal")

        cfg = Log.NORMAL
        if mode == "Verbose":
            cfg = Log.VERBOSE
        elif mode == "Debug":
            cfg = Log.DEBUG

        prefix = ""
        if level == Log.VERBOSE:
            prefix = "[V] "
        elif level == Log.DEBUG:
            prefix = "[D] "

        if level == Log.STATUS:
            Domoticz.Status(str(msg))
            return
        if level == Log.ERROR:
            Domoticz.Error(str(msg))
            return

        if level <= cfg:
            Domoticz.Log(prefix + str(msg))

    # ---- Devices ----

    def _create_or_update_devices(self):
        if not self.add_devices:
            return

        for unit, name, typename, options in DEVICE_DEFS:
            if unit not in Devices:
                Domoticz.Device(
                    Name=name,
                    Unit=unit,
                    TypeName=typename,
                    Options=options,
                    Used=1,
                ).Create()

        self._update_switch(UNIT_LOGIN, False)

    def _update_custom(self, unit, value, force=False):
        if unit not in Devices:
            return
        svalue = "" if value is None else f"{value:.4f}"
        if force or Devices[unit].sValue != svalue or Devices[unit].nValue != 0:
            Devices[unit].Update(nValue=0, sValue=svalue)

    def _update_text(self, unit, text):
        if unit not in Devices:
            return
        text = str(text)
        if Devices[unit].sValue != text or Devices[unit].nValue != 0:
            Devices[unit].Update(nValue=0, sValue=text)

    def _update_text_json(self, unit, data):
        if unit not in Devices:
            return
        try:
            text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        except Exception as e:
            self.displaylog(f"Kon JSON niet serialiseren voor unit {unit}: {e}", Log.ERROR)
            return
        if Devices[unit].sValue != text or Devices[unit].nValue != 0:
            Devices[unit].Update(nValue=0, sValue=text)

    def _update_switch(self, unit, onoff):
        if unit not in Devices:
            return
        nvalue = 1 if onoff else 0
        svalue = "On" if onoff else "Off"
        if Devices[unit].nValue != nvalue or Devices[unit].sValue != svalue:
            Devices[unit].Update(nValue=nvalue, sValue=svalue)

    # ---- Token persistence helpers ----

    def _token_fp(self, token):
        if not token:
            return ""
        raw = json.dumps({
            "token_type": token.get("token_type"),
            "expires_in": token.get("expires_in"),
            "token_obtained_at": token.get("token_obtained_at"),
            "access_token_tail": (token.get("access_token") or "")[-6:],
            "refresh_token_tail": (token.get("refresh_token") or "")[-6:],
        }, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _sync_token_from_api(self, force_save=False):
        if not self.storage or not self.api:
            return

        before = self._token_fp(self.storage.token)
        after = self._token_fp(self.api.token)

        if force_save or (after and after != before):
            self.storage.token = self.api.token
            self.storage.save_token()

    def _clear_auth(self, reason):
        self.displaylog(f"Auth reset: {reason}", Log.STATUS)
        if self.storage:
            self.storage.token = None
            self.storage.save_token()
        if self.api:
            self.api.set_token(None)
        self._update_switch(UNIT_LOGIN, False)
        self._set_status("Niet aangemeld; zet 'Login aanvragen' aan", lock=True)

    # ---- Status helpers ----

    def _set_status(self, text, lock=False):
        text = str(text)

        if not self.storage:
            self._update_text(UNIT_STATUS, text)
            return

        if self.storage.state.get("status_text") == text:
            self._update_text(UNIT_STATUS, text)
            return

        self.storage.state["status_text"] = text
        if lock:
            self.storage.state["status_locked_until_ts"] = time.time() + self.status_lock_seconds

        self.storage.save_state()
        self._update_text(UNIT_STATUS, text)

    def _status_is_locked(self):
        if not self.storage:
            return False
        until = float(self.storage.state.get("status_locked_until_ts", 0) or 0)
        return time.time() < until

    # ---- Daily schedule ----

    def _daily_fetch_due_now(self):
        if not self.storage:
            return False

        now = datetime.now().astimezone()
        now_date = now.strftime("%Y-%m-%d")
        now_ts = now.timestamp()

        last_key = self.storage.state.get("daily_last_fetch_key")
        window = int(DAILY_FETCH_WINDOW_SECONDS)

        for t in DAILY_FETCH_TIMES:
            try:
                hh, mm, ss = map(int, t.split(":"))
            except Exception:
                continue

            target = now.replace(hour=hh, minute=mm, second=ss, microsecond=0)
            key = f"{now_date} {t}"

            if last_key == key:
                continue

            if target.timestamp() <= now_ts < (target.timestamp() + window):
                self.storage.state["daily_last_fetch_key"] = key
                self.storage.save_state()
                return True

        return False

    # ---- Cache fingerprint (today+tomorrow) ----

    def _cache_window_local_bounds(self):
        """
        Returns (start_dt, end_dt) as timezone-aware local datetimes.
        Window: today 00:00:00 up to (but excluding) day after tomorrow 00:00:00.
        """
        now = datetime.now().astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=2)
        return start, end

    def _build_cache_fp_basis(self):
        """
        Build a stable fingerprint basis from caches for today+tomorrow only.
        Includes:
          - summary_cache.data.price_per_hour fields (datetime + electricity + optional gas + group + score)
          - gas_cache fallback meta price (if present)
        """
        start, end = self._cache_window_local_bounds()

        basis = {
            "window_local_start": start.strftime("%Y-%m-%d %H:%M:%S"),
            "window_local_end": end.strftime("%Y-%m-%d %H:%M:%S"),
            "summary_hours": [],
            "gas_fallback_price_raw": None,
        }

        # Summary slice
        summary = self.storage.summary_cache if self.storage else None
        items = None
        if isinstance(summary, dict):
            try:
                items = summary["data"]["price_per_hour"]
            except Exception:
                items = None

        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                dt_s = item.get("datetime")
                if not dt_s:
                    continue
                try:
                    dt = datetime.fromisoformat(dt_s.replace("Z", "+00:00")).astimezone()
                except Exception:
                    continue

                if not (start <= dt < end):
                    continue

                basis["summary_hours"].append([
                    dt_s,
                    item.get("electricity_price"),
                    item.get("electricity_price_excl_tax"),
                    item.get("gas_price"),
                    item.get("tariff_group"),
                    item.get("sustainability_score"),
                ])

        # Sort to be order-independent
        basis["summary_hours"].sort(key=lambda x: x[0])

        # Gas fallback meta price (single value)
        gas = self.storage.gas_cache if self.storage else None
        if isinstance(gas, dict):
            groups = gas.get("data", {}).get("measurement_groups", [])
            if isinstance(groups, list):
                for g in groups:
                    if not isinstance(g, dict):
                        continue
                    if g.get("type") == "hours":
                        meta = g.get("meta", {}) or {}
                        basis["gas_fallback_price_raw"] = meta.get("price")
                        break

        return basis

    def _fp_hash(self, basis_obj):
        raw = json.dumps(basis_obj, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _summarize_cache_changes(self, old_basis, new_basis, limit=12):
        """
        Human-readable diff for the cache fp basis.
        """
        lines = []

        if not isinstance(old_basis, dict):
            old_basis = {}
        if not isinstance(new_basis, dict):
            new_basis = {}

        old_hours = old_basis.get("summary_hours") or []
        new_hours = new_basis.get("summary_hours") or []

        old_map = { (x[0] if isinstance(x, list) and x else ""): x for x in old_hours }
        new_map = { (x[0] if isinstance(x, list) and x else ""): x for x in new_hours }

        keys = sorted(set(old_map.keys()) | set(new_map.keys()))

        def short(v):
            if v is None:
                return "null"
            s = str(v)
            return s if len(s) <= 32 else (s[:29] + "...")

        for k in keys:
            o = old_map.get(k)
            n = new_map.get(k)
            if o is None:
                lines.append(f"+ {k} added")
                continue
            if n is None:
                lines.append(f"- {k} removed")
                continue

            # fields: purchase, sell, gas, group, score (indexes 1..5)
            diffs = []
            labels = ["elec_raw", "sell_raw", "gas_raw", "group", "score"]
            for idx, label in enumerate(labels, start=1):
                ov = o[idx] if len(o) > idx else None
                nv = n[idx] if len(n) > idx else None
                if ov != nv:
                    diffs.append(f"{label}:{short(ov)}->{short(nv)}")
            if diffs:
                lines.append(f"* {k} " + " ".join(diffs))

            if len(lines) >= limit:
                break

        # gas fallback change
        if (old_basis.get("gas_fallback_price_raw") != new_basis.get("gas_fallback_price_raw")):
            lines.append(
                f"* gas_fallback meta.price {short(old_basis.get('gas_fallback_price_raw'))}"
                f"->{short(new_basis.get('gas_fallback_price_raw'))}"
            )

        return lines[:limit]

    # ---- Forecast JSON update gated by cache fingerprint ----

    def _maybe_update_forecast_json(self, force=False):
        """
        Only write Forecast JSON / 'updated' when cache content (today+tomorrow slice) changed.
        """
        if not self.storage:
            return False

        new_basis = self._build_cache_fp_basis()
        new_fp = self._fp_hash(new_basis)

        old_fp = self.storage.state.get("last_cache_fp")
        old_basis = self.storage.state.get("last_cache_fp_basis", {})

        if not (force or new_fp != old_fp):
            return False

        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self.storage.state["last_cache_fp"] = new_fp
        self.storage.state["last_cache_change"] = now_ts
        self.storage.state["last_remote_change"] = now_ts  # for UNIT_LASTUPDATE

        self.storage.state["last_cache_fp_basis_prev"] = old_basis
        self.storage.state["last_cache_fp_basis"] = new_basis

        changes = self._summarize_cache_changes(old_basis, new_basis, limit=12)
        self.storage.state["last_cache_fp_changes"] = changes

        log_list = self.storage.state.get("fp_change_log", [])
        if not isinstance(log_list, list):
            log_list = []
        log_list.append({
            "ts": now_ts,
            "cache_fp": new_fp,
            "force": bool(force),
            "changes": changes,
        })
        log_list = log_list[-self.fp_change_log_limit:]
        self.storage.state["fp_change_log"] = log_list

        self.storage.save_state()

        # Build widget payload (still based on summary/gas caches)
        forecast_payload = self.api.build_forecast_payload_from_summary(
            self.storage.summary_cache,
            self.storage.gas_cache,
            updated=self.storage.state.get("last_cache_change", now_ts),
            past_hours=FORECAST_PAST_HOURS,
            future_hours=FORECAST_FUTURE_HOURS,
        )
        if not forecast_payload:
            return False

        # "updated" visible in widget = last_cache_change
        forecast_payload["updated"] = self.storage.state.get("last_cache_change", now_ts)
        self._update_text_json(UNIT_FORECAST_JSON, forecast_payload)

        return True

    # ---- Auth/login flow ----

    def _is_authenticated(self):
        return bool(
            self.storage and self.storage.token and
            self.storage.token.get("access_token") and self.storage.token.get("refresh_token")
        )

    def _login_cooldown_active(self):
        last_ts = float(self.storage.state.get("last_login_request_ts", 0) or 0)
        return (time.time() - last_ts) < LOGIN_COOLDOWN_SECONDS

    def _start_login_flow(self):
        if not self.email:
            self._set_status("Geen e-mail ingevuld", lock=True)
            self._update_switch(UNIT_LOGIN, False)
            return

        if self._login_cooldown_active():
            last_ts = float(self.storage.state.get("last_login_request_ts", 0) or 0)
            remaining = int(LOGIN_COOLDOWN_SECONDS - (time.time() - last_ts))
            self._set_status(f"Login cooldown actief ({remaining}s)", lock=True)
            self._update_switch(UNIT_LOGIN, False)
            return

        try:
            data = self.api.request_login(self.email)
            login_uuid = data["data"]["uuid"]

            self.storage.state["pending_login_uuid"] = login_uuid
            self.storage.state["pending_login_started_ts"] = time.time()
            self.storage.state["last_login_request_ts"] = time.time()
            self.storage.save_state()

            self.next_poll_login_ts = 0
            self._set_status("Loginmail verzonden; klik op de link", lock=True)
            self._update_switch(UNIT_LOGIN, True)
        except Exception as e:
            self._set_status(f"Login aanvragen mislukt: {e}", lock=True)
            self._update_switch(UNIT_LOGIN, False)
            self.displaylog(f"Login aanvragen mislukt: {e}", Log.ERROR)

    def _cancel_pending_login(self):
        self.storage.state.pop("pending_login_uuid", None)
        self.storage.state.pop("pending_login_started_ts", None)
        self.storage.save_state()
        self._set_status("Login aanvraag geannuleerd", lock=True)
        self._update_switch(UNIT_LOGIN, False)

    def _process_pending_login(self):
        login_uuid = self.storage.state.get("pending_login_uuid")
        if not login_uuid:
            return

        if time.time() < self.next_poll_login_ts:
            return

        started = float(self.storage.state.get("pending_login_started_ts", 0) or 0)
        if started and (time.time() - started) > LOGIN_PENDING_TIMEOUT_SECONDS:
            self._set_status("Login aanvraag verlopen", lock=True)
            self._cancel_pending_login()
            return

        self.next_poll_login_ts = time.time() + 15

        try:
            data = self.api.get_login_request(login_uuid)
            payload = data.get("data", {})

            if payload.get("is_activated") and payload.get("password"):
                token_data = self.api.exchange_one_time_password(email=self.email, password=payload["password"])
                self.storage.token = token_data
                self.api.set_token(token_data)
                self.storage.save_token()

                self.storage.state.pop("pending_login_uuid", None)
                self.storage.state.pop("pending_login_started_ts", None)

                # Automatisch connection UUID ophalen als die nog niet bekend is
                if not self.connection_uuid:
                    try:
                        account = self.api.get_user_account()
                        uuid = self.api.extract_connection_uuid(account, market_segment="electricity")
                        if uuid:
                            self.connection_uuid = uuid
                            self.storage.state["connection_uuid"] = uuid
                            self.displaylog(f"Connection UUID automatisch opgehaald: {uuid}", Log.STATUS)
                        else:
                            self.displaylog("Geen electricity connection gevonden in account", Log.ERROR)
                    except Exception as e:
                        self.displaylog(f"Connection UUID ophalen mislukt: {e}", Log.ERROR)

                self.storage.save_state()

                self._update_switch(UNIT_LOGIN, False)
                self._set_status("Authenticatie OK", lock=True)

                self._fetch_remote_data(force=True)

                force_first = not bool(self.storage.state.get("last_cache_fp"))
                self._maybe_update_forecast_json(force=force_first)
            else:
                if not self._status_is_locked():
                    self._set_status("Wacht op klik in loginmail")
        except Exception as e:
            self.displaylog(f"Polling login status mislukt: {e}", Log.VERBOSE)

    # ---- Fetching/caching ----

    def _fetch_remote_data(self, force=False):
        if not self._is_authenticated():
            return
        if not force:
            return

        self.storage.state["last_remote_fetch"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.storage.save_state()

        got_summary = False
        try:
            summary = self.api.get_summary(self.connection_uuid)
            self._sync_token_from_api()
            self.storage.summary_cache = summary
            self.storage.save_summary_cache()
            got_summary = True
        except Exception as e:
            if "401" in str(e) or "403" in str(e):
                self._clear_auth(f"summary auth error: {e}")
                return
            self.displaylog(f"Summary ophalen mislukt: {e}", Log.ERROR)

        need_gas_fallback = True
        if got_summary:
            prices = self.api.parse_current_prices_from_summary(self.storage.summary_cache)
            if prices and prices.get("gas_price_incl") is not None:
                need_gas_fallback = False

        if need_gas_fallback:
            try:
                gas = self.api.get_gas(self.connection_uuid)
                self._sync_token_from_api()
                self.storage.gas_cache = gas
                self.storage.save_gas_cache()
            except Exception as e:
                if "401" in str(e) or "403" in str(e):
                    self._clear_auth(f"gas auth error: {e}")
                    return
                self.displaylog(f"Gas fallback ophalen mislukt: {e}", Log.VERBOSE)
        else:
            self.displaylog("Gas fallback skip: summary bevat gas_price voor huidig uur.", Log.DEBUG)

    def _update_devices_from_cache(self, update_forecast_json=False):
        prices = self.api.parse_current_prices_from_summary(self.storage.summary_cache)
        gas_fb = self.api.parse_gas_fallback(self.storage.gas_cache)

        elec_incl = elec_sell_ex_tax = gas_incl = None
        timeslot = "n.v.t."

        if prices:
            elec_incl = prices.get("electricity_price_incl")
            elec_sell_ex_tax = prices.get("electricity_sell_price_ex_tax")
            gas_incl = prices.get("gas_price_incl")
            timeslot = prices.get("timeslot", "n.v.t.")

        if gas_incl is None:
            gas_incl = gas_fb.get("gas_price_incl")

        self._update_custom(UNIT_ELEC_INCL, elec_incl)
        self._update_custom(UNIT_ELEC_SELL_EX_TAX, elec_sell_ex_tax)
        self._update_custom(UNIT_GAS_INCL, gas_incl, force=True)

        last_remote_change = self.storage.state.get("last_remote_change", "n.v.t.")
        last_remote_fetch = self.storage.state.get("last_remote_fetch", "n.v.t.")
        self._update_text(UNIT_LASTUPDATE, f"slot={timeslot} | change={last_remote_change} | fetch={last_remote_fetch}")

        if update_forecast_json:
            self._maybe_update_forecast_json(force=False)

        if not self._status_is_locked():
            if self.storage.state.get("pending_login_uuid"):
                self._set_status("Wacht op klik in loginmail")
            elif not self._is_authenticated():
                self._set_status("Niet aangemeld; zet 'Login aanvragen' aan")
            elif elec_incl is None and gas_incl is None:
                self._set_status("Geen prijsdata in cache")
            else:
                self._set_status("Prijsdata OK")


global _plugin
_plugin = Plugin()


def onStart():
    _plugin.onStart()


def onStop():
    _plugin.onStop()


def onHeartbeat():
    _plugin.onHeartbeat()


def onCommand(Unit, Command, Level, Hue):
    _plugin.onCommand(Unit, Command, Level, Hue)
