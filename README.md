# Hehku Energia for Home Assistant

[![Validate](https://github.com/tdragon/hehku-home-assistant/actions/workflows/validate.yml/badge.svg)](https://github.com/tdragon/hehku-home-assistant/actions/workflows/validate.yml)

Unofficial, HACS-compatible Home Assistant integration that imports hourly electricity consumption from the private Eliq Insights API used by the Hehku Energia Android app.

> [!WARNING]
> This project is not affiliated with, endorsed by, or supported by Hehku Energia or Eliq. It depends on a private app API and may stop working without notice. Use it only with your own account.

## Features

- Home Assistant UI configuration using Hehku's email magic-link login
- automatic access-token refresh, including immediate persistence of rotated refresh tokens
- availability-aware collection instead of wasteful hourly polling:
  - consumption shortly after `00:00 UTC`, with two conditional hourly retries
  - next-day spot prices at `12:15 UTC`, with two conditional hourly retries
- a 14-day trailing correction window to pick up delayed and revised readings
- a **Fetch recent consumption** button
- parameterized `hehku_energy.backfill` and `hehku_energy.backfill_spot_prices` actions
- hourly kWh, raw EUR/kWh prices, and cumulative estimated EUR supply cost imported as Home Assistant **external long-term statistics**
- configurable local spot multiplier and VAT-inclusive per-kWh margin; no dependency on Eliq's calculated-cost response
- sensors for current spot price, today's estimated supply cost, month-to-date estimated supply cost, latest consumption, and last import
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

Once configured, the integration performs two primary daily collections:

- consumption at `00:15 UTC`, covering a trailing correction window;
- today's and tomorrow's market prices at `12:15 UTC`.

At `01:15`/`02:15 UTC`, consumption is retried only while yesterday remains incomplete. At `13:15`/`14:15 UTC`, prices are retried only while tomorrow remains incomplete. A restart also refreshes stale working data. Null values remain missing; they are never collapsed or shifted.

Pressing **Fetch recent consumption** refreshes both recent consumption and locally calculated costs immediately. Home Assistant records the sensor state's `last_changed` when the fetch completes; the actual measurement time is the latest-hour sensor's `interval_start` attribute and the timestamp stored with the external statistic.

### Custom backfill

A Home Assistant Button entity cannot accept input fields. Custom intervals are therefore implemented as an action instead of a button. Open **Settings → Developer tools → Actions**, select **Hehku Energia: Backfill consumption**, and choose the dates, or call the action from YAML:

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

### Spot-price backfill and local cost model

Open **Settings → Developer tools → Actions**, select **Hehku Energia: Backfill spot prices**, or call:

```yaml
action: hehku_energy.backfill_spot_prices
data:
  start_date: "2026-08-01"
  end_date: "2026-09-01"
```

The action upserts raw EUR/kWh market prices, pairs them with existing hourly consumption, rebuilds correction-safe cumulative EUR cost statistics, and immediately refreshes daily and month-to-date estimates. Repeating the same range is safe. Dates use the same inclusive-start/exclusive-end convention as consumption backfill.

Configure the calculation from the integration's **Configure** dialog:

```text
interval supply cost = consumption kWh × (spot EUR/kWh × multiplier + margin EUR/kWh)
```

Defaults are a spot multiplier of `1.0` and a VAT-inclusive margin of `0.0035 EUR/kWh`. The multiplication occurs per interval before daily/monthly aggregation. Monthly fees, discounts, Caruna distribution charges, electricity tax, and other invoice charges are intentionally excluded.

## Home Assistant history and Energy Dashboard

The integration creates statistics like:

```text
hehku_energy:<location>_energy_consumption
hehku_energy:<location>_spot_price
hehku_energy:<location>_supply_cost
```

Consumption stores the hourly kWh `state` and cumulative kWh `sum`. Spot price stores the arithmetic hourly EUR/kWh value. Supply cost stores each hour's estimated EUR `state` and its correction-safe cumulative EUR `sum`; daily and monthly values are aggregations of the interval calculations.

The consumption statistic should appear as a grid-consumption source in **Settings → Dashboards → Energy** after the first successful import and Recorder processing. The cumulative supply-cost statistic can be inspected through Home Assistant statistics and used by dashboards that accept external monetary statistics. Informational sensors do not replace the historical statistics.

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

Recent readings may remain null for hours or days due to upstream meter/provider delay. Conditional retries do not imply near-real-time electricity monitoring.

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
