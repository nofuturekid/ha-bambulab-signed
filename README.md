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

## License

MIT, as upstream. Not affiliated with Bambu Lab, greghesp or ClusterM.
