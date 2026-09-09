# Security policy

## Reporting a vulnerability

Please report credential exposure, authentication bypasses, or cross-account data access privately through [GitHub private vulnerability reporting](https://github.com/tdragon/hehku-home-assistant/security/advisories/new). Do not open a public issue containing tokens, ticket links, email addresses, location IDs, addresses, customer numbers, metering-point identifiers, HAR files, or copied authenticated requests.

## Credential handling

The integration stores the Eliq access token, renewable refresh token, user ID, and generated device UUID in the Home Assistant config entry. Protect Home Assistant backups accordingly. Refresh tokens travel in a legacy API query parameter; this integration deliberately avoids logging request URLs and response bodies from authentication failures.

If credentials are exposed, remove and re-add the integration and revoke access through Hehku/Eliq if that option is available.
