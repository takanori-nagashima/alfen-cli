# alfen-cli

Command line client for the local HTTPS API of Alfen Eve / NG9xx EV chargers
(the API that ACE Service Installer and the charger's web UI use).

```bash
pip install alfen-cli            # add [signed] for `alfen verify`
export ALFEN_HOST=192.168.1.20 ALFEN_USERNAME=admin ALFEN_PASSWORD=...

alfen info                       # identity / model / firmware, no login
alfen categories
alfen get -c ocpp                # all properties in a category
alfen get 2126_0 OD_commBackOfficeURL_serverDomainAndPort
alfen set 20F2_3=ws://csms.example:8081 --string
alfen logs -n 100                # event log, plain text (unlike OCPP GetLog, which is encrypted)
alfen logs -f                    # follow
alfen transactions
alfen whitelist
alfen reboot --yes
alfen verify <meterPublicKey> <signedMeterData>   # Alfen/DZG signed meter value
```

`--json` gives machine-readable output.

## One session at a time

The charger's web API serves **one client at a time**. While ACE Service Installer, the
charger's web UI in a browser, Home Assistant (alfen_wallbox) or another script is connected,
every other connection is dropped right after the TCP connect or the TLS ClientHello:

```
error: ConnectionError: ... Connection reset by peer
```

The charger log shows it as `TLS handshake failed (-0x50)` / `WebServer state AVAILABLE -> BUSY`.
Close the other client (in ACE Service Installer: disconnect, or quit the program) and retry.
Wrong credentials look different (HTTP 401/403 from `/api/login`). `alfen` logs out when each
command finishes, so it does not hold the session itself; `alfen logs -f` does, until you stop it.

## Notes
- The charger uses a self-signed certificate and old TLS ciphers; certificate checks are off.
- Repeated wrong passwords can lock the account. Check credentials before scripting.
- `set` writes raw property ids. Some changes only apply after `reboot`.

## Credits

Property id → name mapping and the API flow are based on
[alfeneve](https://gitlab.com/LordGaav/alfen-eve) (MIT, Nick Douma).

## Development

Source: https://gitea.ai-charge.net/AI-ChargeTechnologies/alfen-cli
(push-mirrored to https://github.com/takanori-nagashima/alfen-cli).

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[signed]' pytest build twine
.venv/bin/pytest
# release: bump __version__ in src/alfen_cli/__init__.py, tag vX.Y.Z, then
.venv/bin/python -m build && .venv/bin/twine upload dist/*   # token: Infisical PIP_TOKEN
```
