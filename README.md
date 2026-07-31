# Zonneplan Domoticz Plugin

Unofficial [Domoticz](https://www.domoticz.com/) plugin for [Zonneplan](https://www.zonneplan.nl/) that fetches dynamic electricity and gas prices and exposes them as devices in Domoticz.

## Features

- **Automatic connection UUID discovery** — no need to look up or fill in any UUIDs manually
- **Quarter-hour electricity price** (€/kWh, active 15-minute slot, incl. VAT)
- **Electricity sell price excluding energy tax** (€/kWh, active 15-minute slot)
- **Dynamic gas price** (€/m³, incl. VAT)
- **Forecast JSON** — quarter-hour forecast for custom widgets (past 2h + next 38h)
- **Hourly fallback** — keeps working if the public quarter-hour endpoint is temporarily unavailable
- **Fingerprint-based timestamp** — `updated` changes only when remote data changes
- **Built-in login flow** — authenticate directly from Domoticz via magic link email, no external tools needed
- **Automatic token refresh** — stays authenticated without manual intervention

## Devices created

| Unit | Name | Type |
|------|------|------|
| 1 | Actual Electricity Price | Custom sensor (€/kWh) |
| 2 | Electricity Sell Price (ex energy tax) | Custom sensor (€/kWh) |
| 3 | Actual Gas Price | Custom sensor (€/m³) |
| 5 | Zonneplan - Status | Text |
| 6 | Login | Switch |
| 7 | Zonneplan - Update | Text |
| 8 | Forecast JSON | Text |

## Requirements

- Domoticz (any recent version with Python plugin support)
- Python 3.6+
- `requests` library:

```bash
pip3 install requests
```

## Installation

1. Go to your Domoticz plugins folder:

```bash
cd /opt/domoticz/userdata/plugins
```

2. Clone this repository:

```bash
git clone https://github.com/frepke/Zonneplan-Domoticz-Plugin.git
```

3. Restart Domoticz:

```bash
sudo systemctl restart domoticz
```

4. In Domoticz, go to **Setup → Hardware** and add a new device:
   - Type: `Zonneplan Prices`
   - Fill in your Zonneplan e-mail address
   - Leave all other fields at their defaults

5. Click **Add**, then find the **Login** switch in your devices and turn it **On**

6. Check your inbox — click the link in the Zonneplan login email

7. Done. The plugin authenticates, discovers your connection UUID automatically, and starts fetching prices.

## Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| Zonneplan e-mail | Your Zonneplan account email | _(required)_ |
| Add missing devices | Automatically create Domoticz devices on startup | Yes |
| Fallback refresh interval | How often to refresh if scheduled fetch is missed | 30 min |
| Log level | Normal / Verbose / Debug | Normal |

## How it works

Price data is fetched from the Zonneplan API at every full hour (00:00–23:00). On startup, data is fetched immediately if already authenticated. The cached quarter-hour chart is evaluated every heartbeat, so the active devices switch locally at `:00`, `:15`, `:30` and `:45` without extra API calls. A slot change is written to Domoticz even if two consecutive prices are identical.

The public endpoint `/api/consumer-prices/charts/electricity-quarter-hourly` is the primary electricity source. The authenticated summary remains the fallback electricity source and supplies the current gas price where available.

The base sell price comes directly from Zonneplan's
`electricity_price_excl_tax` API field. It is not calculated by subtracting a
fixed tax amount, so future energy-tax changes do not require a plugin setting.
The conditional 10% Zonnebonus is not included in this API field. Negative sell
prices are preserved so the value can be used directly by export-limiting
automations.

The Forecast JSON contains `electricity_sell_now_ex_tax` for the current slot
and both `sell_price_ex_tax_raw` and `sell_price_ex_tax` for every item in
`hours`. Existing widget keys are preserved. Version 1.2 adds `source`,
`interval_minutes`, `end_datetime` and `local_end_datetime`. The `updated`
timestamp changes only when remote content changes; `is_current` and the two
current electricity values are refreshed at every slot boundary.

## Upgrade from v1.1

Stop Domoticz, replace the plugin files with the files from this archive, and start Domoticz again. Keep the existing `data/` folder: it contains the login token, connection UUID and state. The plugin creates `data/electricity_quarter_hourly_cache.json` automatically.

The existing hardware entry and devices are reused, so their IDX numbers do not change.

After login, the plugin calls `/user-accounts/me` to discover your electricity connection UUID automatically and stores it locally — you never need to find or enter it yourself.

## File structure

```
plugin.py          # Main Domoticz plugin
zonneplan_api.py   # Zonneplan API client
storage.py         # JSON-based local storage
constants.py       # Configuration constants
```

## Data folder (auto-created, never committed)

```
data/
  token.json         # OAuth tokens — keep private
  state.json         # Plugin state (UUID, fingerprints, timestamps)
  summary_cache.json # Cached API response
  gas_cache.json     # Cached gas API response
```

## License

MIT — see [LICENSE](LICENSE)

## Disclaimer

This plugin uses the unofficial Zonneplan API. It is not affiliated with or endorsed by Zonneplan. Use at your own risk.
