---
title: TR-020 — Private-LAN Garmin sign-in
type: user-story
status: implemented-live-review-required
owner: project
audience: operator-and-developer
updated: 2026-08-06
---

# TR-020 — Private-LAN Garmin sign-in

## Outcome

Allow a household operator to perform the one-time experimental Garmin activity-upload login and MFA from another PC or phone whose direct peer address is private or link-local. After authentication, the NUC continues unattended uploads using only the runner-specific DPAPI-protected Garmin session token.

## Accepted security trade-off

The owner explicitly chose private-LAN HTTP convenience after being informed that HTTP does not encrypt the Garmin password or MFA code in transit. HTTPS remains preferred. Loopback, RFC1918 IPv4, IPv4 link-local, IPv4-mapped private addresses, IPv6 ULA, and IPv6 link-local peers are accepted. A public or missing HTTP peer is rejected with HTTP 426.

This permission is not an internet-access feature. TreadmillRunner must remain behind the installer-created Windows Firewall Private-profile/local-subnet rule and must never be port-forwarded, placed on a guest network, or exposed through a proxy that hides an untrusted original peer as a trusted local address.

## Acceptance

- Address-class tests prove private/local acceptance and public rejection without using real credentials.
- The profile panel labels the adapter **Experimental**, warns that private-LAN HTTP is unencrypted, and shows safe endpoint errors.
- Passwords and MFA codes remain transient form/process values and never enter SQLite, configuration, logs, screenshots, or diagnostics.
- The existing connected profile can upload a clearly identified TreadmillRunner-generated FIT acceptance activity without treadmill commands. The result must be recorded as Confirmed, Failed, or Unknown; Unknown is never retried blindly.

## Recovery

If Garmin authentication expires, reconnect only from the NUC, trusted HTTPS, or a trusted household-LAN device. Successful connection stores the encrypted session token, not the password. See [Garmin integrations](../garmin-connect.md).

## Live acceptance result

Signed local versions 1.5.11 and 1.5.12 were activated through the normal update helper. Version 1.5.12 is healthy, the account remains connected/enabled, and a live request through the NUC's RFC1918 address reached credential validation instead of HTTP 426, proving the private-LAN gate is active.

Exactly one synthetic activity was queued with operation/session ID `e2b339bf-727a-48d4-bce5-98af1076c4e3`. Garmin's import POST completed without an activity ID and the installed 1.5.11 adapter conservatively recorded job `3de07e73-bdc8-49bb-8e1e-4b05ac5230bc` as `Unknown`, `CanRetry=false`. It was not resent. The pinned library source showed that its `{"status":"uploaded"}` fallback is emitted only after a successful import POST, so 1.5.12 now treats that response as Confirmed for future activities even when Garmin omits the remote ID. The already-Unknown test remains unchanged and must be checked manually in Garmin Connect around 2026-08-06 00:39–00:40 local time.
