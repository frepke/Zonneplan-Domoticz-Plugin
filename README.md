# Zonneplan Domoticz Plugin

Unofficial [Domoticz](https://www.domoticz.com/) plugin for [Zonneplan](https://www.zonneplan.nl/) that fetches dynamic electricity and gas prices and exposes them as devices in Domoticz.

## Features

- **Automatic connection UUID discovery** — no need to look up or fill in any UUIDs manually
- **Dynamic electricity price** (€/kWh, current hour, incl. VAT)
- **Dynamic gas price** (€/m³, incl. VAT)
- **Forecast JSON** — full price forecast for use in custom widgets (past 2h + next 38h)
- **Fingerprint-based updates** — forecast is only written when data actually changes
- **Built-in login flow** — authenticate directly from Domoticz via magic link email, no external tools needed
- **Automatic token refresh** — stays authenticated without manual intervention

## Devices created

| Unit | Name | Type |
|------|------|------|
| 1 | Actual Electricity Price | Custom sensor (€/kWh) |
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

Price data is fetched from the Zonneplan API at every full hour (00:00–23:00). On startup, data is fetched immediately if already authenticated. The forecast JSON device contains a full JSON payload intended for use with a custom Domoticz widget.

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
