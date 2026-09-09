# Hehku Energia for Home Assistant

[![Validate](https://github.com/tdragon/hehku-home-assistant/actions/workflows/validate.yml/badge.svg)](https://github.com/tdragon/hehku-home-assistant/actions/workflows/validate.yml)

Unofficial, HACS-compatible Home Assistant integration that imports hourly electricity consumption from the private Eliq Insights API used by the Hehku Energia Android app.

> [!WARNING]
> This project is not affiliated with, endorsed by, or supported by Hehku Energia or Eliq. It depends on a private app API and may stop working without notice. Use it only with your own account.

## Features

- Home Assistant UI configuration using Hehku's email magic-link login
- automatic access-token refresh, including immediate persistence of rotated refresh tokens
- hourly cloud polling
- a 14-day trailing correction window to pick up delayed and revised readings
- a **Fetch recent consumption** button
- a parameterized `hehku_energy.backfill` action for custom historical intervals
- hourly kWh imported as Home Assistant **external long-term statistics**
- a latest-hour sensor and last-import diagnostic sensor
- DST-aware interval reconstruction: spring skips local 03:00 and autumn represents both 03:00 folds; an unexpected API slot count is skipped rather than guessed

## Requirements

- Home Assistant 2026.2.0 or newer
- an activated Hehku account whose email magic-link login works
- Recorder enabled in Home Assistant

## Installation with HACS

1. Open HACS.
2. Open the three-dot menu and choose **Custom repositories**.
3. Add `https://github.com/tdragon/hehku-home-assistant` as category **Integration**.
4. Install **Hehku Energia**.
5. Restart Home Assistant.
6. Go to **Settings → Devices & services → Add integration** and search for **Hehku Energia**.
7. Enter your Hehku email and open the received link. The flow continues automatically.

Manual installation is also possible by copying `custom_components/hehku_energy` into your Home Assistant `config/custom_components` directory and restarting.

## Automatic and manual collection

Once configured, the integration polls once an hour. Each poll fetches the previous 14 local calendar days plus the current day. Null values remain missing; they are never collapsed or shifted.

Pressing **Fetch recent consumption** triggers that same correction-window poll immediately.

### Custom backfill

A Home Assistant Button entity cannot accept input fields. Custom intervals are therefore implemented as an action instead of a button:

```yaml
action: hehku_energy.backfill
data:
  start_date: "2026-05-01"
  end_date: "2026-09-10"
```

`start_date` is inclusive and `end_date` is exclusive. If `end_date` is omitted, it defaults to tomorrow in `Europe/Helsinki`.

When only one Hehku entry exists, `config_entry_id` is optional. With several entries, choose the entry in the action UI or provide its ID:

```yaml
action: hehku_energy.backfill
data:
  config_entry_id: 01J_EXAMPLE
  start_date: "2026-05-01"
```

The API's documented hourly-request maximum is 93 days; the integration uses chunks of at most 92 days. A manual request—including any automatic extension needed to repair later cumulative sums—is limited to three years.

If an older interval is added or corrected, all later existing sums are rebuilt through the newest imported date. This matters because Home Assistant's Energy Dashboard expects cumulative `sum` statistics, not isolated hourly deltas.

## Home Assistant history and Energy Dashboard

Yes—Home Assistant supports this use case through Recorder's external long-term statistics. The integration creates a statistic like:

```text
hehku_energy:<location>_energy_consumption
```

It stores:

- `state`: that hour's consumption in kWh
- `sum`: cumulative imported consumption in kWh
- `start`: timezone-aware UTC start of the interval

The statistic should appear as a grid-consumption source in **Settings → Dashboards → Energy** after the first successful import and Recorder processing. It is not represented by the latest-hour sensor's state; the sensor is informational only.

## Timestamp and DST safety

The Eliq response contains a positional array but no timestamp for each value. Request boundaries are naive local datetimes. Normal Finnish dates have been validated, including conversion from raw Wh to kWh and exclusive `to` behavior.

Caruna's source data has been observed to use 23 hourly intervals on the Finnish spring transition (local 03:00 absent) and 25 intervals on the autumn transition (local 03:00 repeated). An older Eliq query for 2025-10-26 returned a 24-null-slot skeleton because the contract had no data for that date; that empty response does not prove that populated Eliq responses flatten DST days to 24 slots.

The integration:

1. uses `Europe/Helsinki`, rather than Eliq's observed incorrect `Europe/Kiev` label;
2. constructs real interval starts by iterating in UTC;
3. isolates 23/25-hour transition dates into one-day requests;
4. maps a populated 23-slot spring response by omitting local 03:00 and maps a 25-slot autumn response to both distinct 03:00 folds;
5. skips only a transition date whose Eliq slot count is unexpected, while continuing to import surrounding normal dates.

## Data units and latency

The private API requests `unit=energy`, but the returned numeric values are **Wh**. This integration divides values by 1,000 before importing kWh.

Recent readings may remain null for hours or days due to upstream meter/provider delay. Hourly polling does not imply near-real-time electricity monitoring.

## Security and privacy

- Never post config entries, access/refresh tokens, magic links, ticket IDs, copied authenticated URLs, HAR captures, addresses, customer numbers, location IDs, or metering-point identifiers in issues.
- The integration does not log credentials or authentication response bodies.
- The legacy refresh endpoint places the refresh token in its query string, so the client deliberately sanitizes failures instead of exposing request URLs.
- Home Assistant backups contain config-entry credentials and must be protected.

See [SECURITY.md](SECURITY.md).

## Development

```bash
uv sync --group dev --python 3.13
uv run pytest
uv run ruff check custom_components tests
uv run ruff format --check custom_components tests
uv run python -m compileall -q custom_components tests
```

CI also runs Home Assistant `hassfest` and `hacs/action` validation.

## License

MIT
