# Garmin activity adapter third-party notices

The optional Garmin activity-upload adapter is experimental, disabled per profile by default, and uses Garmin's private consumer interface. It may stop working without notice.

Signed Windows release packages contain:

- CPython 3.12.10 (Python Software Foundation License). The complete `runtime/LICENSE.txt` is shipped beside `python.exe`.
- `garminconnect` 0.3.8 (MIT)
- `curl_cffi` 0.16.0 (MIT)
- `requests` 2.34.2 (Apache-2.0)
- `ua-generator` 2.1.3 (MIT)
- `cffi` 2.1.1 (MIT)
- `certifi` 2026.7.22 (MPL-2.0)
- `charset-normalizer` 3.4.9 (MIT)
- `idna` 3.18 (BSD-3-Clause)
- `urllib3` 2.7.0 (MIT)
- `pycparser` 3.0 (BSD-3-Clause)

Package metadata and upstream license files are retained in `runtime/Lib/site-packages/*-dist-info`. Versions and artifact hashes are fixed by `requirements.lock.txt`; ordinary installation never runs `pip` or downloads Python packages.
