# agentcheck fixtures

- `cloudflare-managed-robots.txt` - the "Feature enabled" example from Cloudflare's
  own managed robots.txt docs (cloudflare/cloudflare-docs,
  `src/content/docs/bots/additional-configurations/managed-robots-txt.mdx`, read
  2026-10-03). It is the origin file of www.crawlstop.com with Cloudflare's managed
  block PREPENDED, which produces two `User-agent: *` groups. crawlstop.com had the
  feature switched off when checked, so the docs copy is the reproducible one.
- `cloudflare-ai-catalog.json` - developers.cloudflare.com/.well-known/ai-catalog.json,
  captured 2026-10-03. A real, conformant ARD catalog that uses extension media
  types (`application/vnd.oai.openapi+json`, `text/plain`). The control that a
  validator rejecting unregistered types is wrong: the ARD conformance suite
  (ards-project/ard-spec `conformance/bin/conformance-test`, which Lighthouse's
  `ard-schema` audit ports directly) accepts any valid IANA media type.
