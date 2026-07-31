# Changelog

## 1.2.0 — 2026-07-31

- Use Zonneplan's public quarter-hour electricity endpoint.
- Select exactly one active slot at `:00`, `:15`, `:30` and `:45`.
- Refresh current devices and Forecast JSON at every slot boundary.
- Interpret forecast limits as hours instead of fixed item counts.
- Preserve the v1.1 Forecast JSON keys and add explicit interval metadata.
- Retain the authenticated summary as electricity fallback and gas source.
- Store the quarter-hour response in a separate persistent cache.
