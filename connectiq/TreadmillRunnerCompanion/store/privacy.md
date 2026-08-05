# Privacy disclosure

TreadmillRunner Companion can operate without an account. When used standalone, activity recording is handled by the Garmin watch and Garmin Connect under the runner's Garmin account and Garmin's privacy terms.

Optional TreadmillRunner pairing sends a bearer token to the runner's configured household gateway over HTTPS. The gateway uses that token only to identify one local TreadmillRunner profile and return the runner name, current session title, and session state. The watch stores the configured gateway URL, pairing token, and fallback runner name in Connect IQ application properties.

The companion does not receive a Garmin password, does not send treadmill commands, does not collect advertising identifiers, does not sell information, and does not contact a TreadmillRunner-operated cloud service. The household operator can revoke a watch token from the profile at any time. Uninstalling the watch app removes its local settings; revocation removes server-side access.

The separately optional TreadmillRunner **unsupported Garmin activity upload** feature runs on the NUC and is not used by this watch app. Its disclosure and controls are shown separately in the TreadmillRunner profile UI.

Before public submission, replace the support/privacy URLs in the IQ Store form with stable HTTPS pages containing this disclosure and operator contact information.
