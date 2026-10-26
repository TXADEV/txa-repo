<!-- Prefer `txacreate new <installer-url>`: it fills this in for you. -->

### Release

- **Package:** `TXAVL.<AppName>`
- **Version:** `X.Y.Z`

### Checklist

- [ ] One release per pull request (one `manifests/<shard>/<Namespace>/<AppName>/<X.Y.Z>/` folder)
- [ ] Only `core.txa.yaml`, `build.txa.yaml` and `about.<lang>.txa.yaml` in that folder
- [ ] `txaci validate <folder>` passes locally
- [ ] The installer URL is the vendor's official HTTPS download

The TXA Bot runs the 16-step pipeline and labels this PR. A moderator (code owner)
must approve before the bot's squash auto-merge can complete.
