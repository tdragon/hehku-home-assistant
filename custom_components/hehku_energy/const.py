"""Constants for the Hehku Energia integration."""

DOMAIN = "hehku_energy"
NAME = "Hehku Energia"
PLATFORMS = ["button", "sensor"]

CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_USER_ID = "user_id"
CONF_DEVICE_UUID = "device_uuid"
CONF_LOCATION_ID = "location_id"
CONF_LOCATION_NAME = "location_name"
CONF_TIME_ZONE = "time_zone"
CONF_SPOT_MULTIPLIER = "spot_multiplier"
CONF_MARGIN = "margin"

DEFAULT_TIME_ZONE = "Europe/Helsinki"
DEFAULT_SPOT_MULTIPLIER = 1.0
DEFAULT_MARGIN = 0.0035
TRAILING_DAYS = 14
MAX_BACKFILL_DAYS = 366 * 3
SERVICE_BACKFILL = "backfill"
SERVICE_BACKFILL_PRICES = "backfill_spot_prices"
ATTR_START_DATE = "start_date"
ATTR_END_DATE = "end_date"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
