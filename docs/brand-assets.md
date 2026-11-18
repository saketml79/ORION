# Header brand assets

Updated: 2026-10-30.

The Databricks logos are unmodified SVGs embedded as base64 image data in
https://www.databricks.com, retrieved on 2026-10-30 with a read-only urllib HTML
request and base64 decoding. Neither variant is a recreated or recolored mark.

- Light theme: [black wordmark](../workbench/static/databricks-official.svg), preserved unchanged.
	SHA-256: 8c6b1fbc7609d5246f85629ca4bf729585694e030245676fa49c309f9673f42f.
- Dark theme: [white wordmark](../workbench/static/databricks-official-dark.svg),
	exact decoded first-party bytes, width 132, height 22, red #EE3D2C and white #fff.
	SHA-256: 216bdd6d49257083e697b39834766f69e29de0285c73c199d1f7d2a9a044899f.

Databricks trademarks remain the property of their respective owner. Placement
identifies the target platform, not endorsement or certification. The full-color
wordmarks sit on a transparent, borderless container in both themes. CSS selects
the white wordmark only under data-theme="dark", otherwise the black wordmark.
Neither SVG contains a background rectangle. No CSS filtering or inversion changes
the brand red. One accessible image label describes both decorative image variants.

ORION's constellation is original artwork. Its gentle star twinkle begins after
ten seconds. The 16-second cycle spends most of its time still, leaving roughly
eleven seconds of rest between twinkles. Reduced-motion mode disables animation.
The SVG is self-contained, so it remains visible without JavaScript or network
access to third-party image servers.