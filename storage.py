# -*- coding: utf-8 -*-

import json
import os


class Storage:
    def __init__(self, home_folder, logger):
        self._log = logger
        self.base_dir = os.path.join(home_folder, "data")

        self.token_file = os.path.join(self.base_dir, "token.json")
        self.state_file = os.path.join(self.base_dir, "state.json")
        self.summary_cache_file = os.path.join(self.base_dir, "summary_cache.json")
        self.electricity_cache_file = os.path.join(self.base_dir, "electricity_quarter_hourly_cache.json")
        self.gas_cache_file = os.path.join(self.base_dir, "gas_cache.json")

        self.token = None
        self.state = {}
        self.summary_cache = None
        self.electricity_cache = None
        self.gas_cache = None

        os.makedirs(self.base_dir, exist_ok=True)

    def _load_json(self, path, default):
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self._log(f"Kon JSON niet laden: {path} | {e}", 2)
            return default

    def _save_json(self, path, data):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self._log(f"Kon JSON niet opslaan: {path} | {e}", 5)

    def load_all(self):
        self.token = self._load_json(self.token_file, default=None)
        self.state = self._load_json(self.state_file, default={})
        self.summary_cache = self._load_json(self.summary_cache_file, default=None)
        self.electricity_cache = self._load_json(self.electricity_cache_file, default=None)
        self.gas_cache = self._load_json(self.gas_cache_file, default=None)

    def save_all(self):
        self.save_state()
        if self.token is not None:
            self.save_token()
        if self.summary_cache is not None:
            self.save_summary_cache()
        if self.electricity_cache is not None:
            self.save_electricity_cache()
        if self.gas_cache is not None:
            self.save_gas_cache()

    def save_state(self):
        self._save_json(self.state_file, self.state)

    def save_token(self):
        self._save_json(self.token_file, self.token)

    def save_summary_cache(self):
        self._save_json(self.summary_cache_file, self.summary_cache)

    def save_electricity_cache(self):
        self._save_json(self.electricity_cache_file, self.electricity_cache)

    def save_gas_cache(self):
        self._save_json(self.gas_cache_file, self.gas_cache)
