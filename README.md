# ha-bambulab-signed

[greghesp/ha-bambulab](https://github.com/greghesp/ha-bambulab), the Home Assistant integration for Bambu Lab printers, rebuilt from every upstream release with a small patch set that **signs MQTT control commands**. With it, printers that stay in Bambu cloud mode (no Developer/LAN-only mode) accept commands from Home Assistant again: target temperatures, fans, pause/resume/stop, print speed.

Nothing else differs from upstream. Each release is `<upstream version>.<build>`, e.g. `2.2.26.1` = upstream 2.2.26 + patches, build 1.

## Why

Current Bambu firmware rejects unsigned control commands from third-party clients (`84033543`, HMS `0500_0500_0001_0007`, "MQTT command verification failed"). Read-only data, camera and lights keep working, but every `print.*` command needs an RSA signature from an application certificate the printer trusts.

## What the patches do

| Patch         | Source                                                                                           | Change                                                                                                                                                                                                                                 |
| ------------- | ------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `0001`–`0003` | [ClusterM/ha-bambulab `mqtt_signing`](https://github.com/ClusterM/ha-bambulab/tree/mqtt_signing) | Signs every `print` request, installs the application certificate on the printer (`security.app_cert_install`) on connect, adds the certificate/key/CRL fields to the options, enables the control entities once signing is configured |
| `0004`        | this repository                                                                                  | Emits the envelope the printer actually accepted (compact JSON with sorted keys, `*_enc` replacing plaintext `url`/`param`, counter sequence ids); drops a command it cannot sign instead of sending it unsigned                       |
| `0005`        | this repository                                                                                  | Renews the CRL automatically (see below) and warns in Home Assistant when renewal is not possible                                                                                                                                      |
| `0006`        | this repository                                                                                  | Sets the signing state in upstream's `PrintFun` and H3C tests (stock: unsigned) and covers that no "control blocked" event fires and the prompt sound stays available when commands can be signed                                      |

The signing approach follows [open-bamboo-networking](https://github.com/ClusterM/open-bamboo-networking). See also the more defensive, fan-only upstream proposal [#2121](https://github.com/greghesp/ha-bambulab/pull/2121).

Verified on hardware: X1C, firmware 01.12.00.00, cloud mode — signed `M106` (fan) and `M140` (bed temperature) executed. Not yet verified: pause/resume/stop, print speed, AMS, starting prints.

## Credentials

The integration needs an application certificate chain, its private key and the current CRL, the same material the official slicer networking plugin uses. Obtain them from **your own** slicer installation, e.g. with [BambuKeySaver](https://github.com/Bambuzled/BambuKeySaver). This repository contains and distributes no keys or certificates.

Enter them under **Settings → Devices & services → Bambu Lab → Configure → Advanced options** (`Slicer certificate` = the full chain, `Slicer private key`, `Slicer CRL`).

### Automatic CRL renewal

The CRL is valid for one month, and the printer rejects an expired one. Bambu issues a fresh CRL whenever the plugin's certificate URL is requested again, so the integration can renew it on its own:

1. Save the URL the capture produced (BambuKeySaver: `trigger-url.txt`) as `/config/.storage/bambu_lab/slicer_refresh/<printer serial>.url` (one line, mode 600). Treat it like a password: its query string is a long-lived credential.
2. Every 12 hours the integration checks the installed CRL. Once it expires within 10 days, it fetches the URL, verifies that the new CRL is signed by the returned chain, currently valid, newer than the installed one and does not revoke the certificate, then stores it in the options and reloads. If the leaf certificate itself was rotated, the new key is decrypted and must match it.
3. Without a URL, or if renewal fails, a notification appears in Home Assistant 7 days before expiry.

## Install (HACS)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/nofuturekid/ha-bambulab-signed`, type _Integration_.
2. If greghesp's Bambu Lab is installed, remove it in HACS first — both use the domain `bambu_lab`. Your config entry and entities stay.
3. Download **Bambu Lab (signed commands)** and restart Home Assistant.

Updates appear in HACS like any other integration.

## How releases are built

[`.github/workflows/release.yml`](.github/workflows/release.yml) runs daily and on changes to the patches:

1. Looks up the latest greghesp release.
2. If that release with the current patch set was not built yet: clones it, applies `patches/*.patch` (`git am --3way`), runs upstream's `tests/pybambu`, the translation parity check and [`tests/test_signed_envelope.py`](tests/test_signed_envelope.py), and zips the integration ([`build.sh`](build.sh)).
3. Publishes a release with a build provenance attestation:
   `gh attestation verify bambu_lab.zip -R nofuturekid/ha-bambulab-signed`

If a patch no longer applies or a test fails, the workflow opens an issue and the previous release remains current. A second job opens an issue when ClusterM's branch moves past the pinned commit in `patches/clusterm-mqtt_signing.sha`.

Build locally: `./build.sh v2.2.26 1 dist` (needs `tests/requirements.txt` from upstream plus `cryptography`).

Pull requests run [`pr-ci.yml`](.github/workflows/pr-ci.yml) (read-only): `build.sh` on the latest upstream release and the mutation check [`scripts/mutate.sh`](scripts/mutate.sh), which applies each mutation in [`scripts/mutations.txt`](scripts/mutations.txt) (envelope format, `*_enc`, sequence ids, both drop-instead-of-unsigned paths, the CRL checks) and requires a test to fail for every one.

## Automatic ports

When a daily build fails because the patches no longer apply to a new upstream release, [`auto-port.yml`](.github/workflows/auto-port.yml) ports them, reviews the port and, if every gate holds, merges and releases it without a human:

1. **Detect** (after a failed `Build release` run, or manually with an upstream tag): the latest upstream release, the same `git am --3way` check as `build.sh`. If the patches apply, nothing happens. An open "Auto-port failed on upstream …" issue or an existing `auto-port/<tag>` branch stops a second attempt.
2. **Port**: Claude Code ([claude-code-action](https://github.com/anthropics/claude-code-action)) applies the series to the new release, resolves conflicts, compares with ClusterM's `mqtt_signing`, regenerates the patches and writes the pull request text. Its token is read-only and its only secret is `CLAUDE_CODE_OAUTH_TOKEN`, so nothing from upstream or the patches runs in this job, and Claude has no shell and no network tool: only file tools (edits limited to the upstream clone, the ClusterM pin and the PR text) and the fixed git and port-script operations of [`scripts/port/port_mcp.py`](scripts/port/port_mcp.py), which check every argument and run from a read-only copy of `main`. The scripts are [`scripts/rebase-series.sh`](scripts/rebase-series.sh), the resolvers in [`scripts/port/`](scripts/port) and [`scripts/export-series.sh`](scripts/export-series.sh).
3. **Publish**: a job without Claude takes only `patches/*.patch` and `patches/clusterm-mqtt_signing.sha` from the porter's output, commits them to `auto-port/<tag>` through the GitHub API (a commit GitHub signs) and opens the pull request. A port that reproduces the current patch set ends here.
4. **PR CI** (no secrets, read-only) on that exact commit, with `build.sh`, `scripts/` and `tests/` taken from `main`, then an **independent review**: a second Claude run with a fresh context, a read-only token, no shell and no edit tools, focused on the signing invariants and behaviour changes, answers `MERGE` or `FIX` as structured output. The review is posted on the pull request.
5. **Gate**, a job without Claude that runs from `main` as it was when the port started (never from the pull request). The verdict comes from the review job's output, not from a file. All must hold:
   - PR CI succeeded, and on the commit that would be merged;
   - the review verdict is `MERGE`;
   - `tests/test_signed_envelope.py` and `scripts/mutations.txt` are unchanged and nothing outside `patches/` changed (`git diff`);
   - the mutation report lists every mutation from `main`'s `scripts/mutations.txt` as killed;
   - the commit is GitHub-verified and `main` has not moved, so landing is a fast-forward.
6. **One fix round**: if anything after publishing fails, the porter gets the review findings and gate results once, and steps 3-5 run again. There is no further retry.
7. **Land**: `main` is fast-forwarded to the gated commit and `release.yml` is started; it builds and attests the release as for any other change.

Any failure opens or updates one issue assigned to the owner, with the failing step and links. Nothing is merged or released then. Installing an update in Home Assistant stays manual.

`workflow_dispatch` with `force` runs the porter even when the patches apply; such a dry run never merges.

## License

MIT, as upstream. Not affiliated with Bambu Lab, greghesp or ClusterM.
