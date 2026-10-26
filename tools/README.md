# txa

Install Windows software from the command line. TXA downloads installers from the vendor's
official site, verifies the SHA-256 and digital signature, then installs silently.

```
pipx install txa
```

```
txapm search notepad
txapm install TXAVL.HelloTool
txapm upgrade --all
txapm uninstall TXAVL.HelloTool
```

| Command | Purpose |
|---|---|
| `txapm` | search, install, upgrade, uninstall, list, export/import packages |
| `txacreate` | sign in with GitHub and submit installers to the repository (`login`, `new`, `update`, `hash`) |
| `txaci` | the 16-step package validator used by the repository bot |

Requires Windows 10 1809 or newer. Full guide (Vietnamese): https://github.com/TXAVL/txa-repo
