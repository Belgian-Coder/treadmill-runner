# Sanitized failure summary — TR-023

Three implementation-time failures were found and resolved:

1. A clean test database returned no maintenance policy. The Run page now
   treats HTTP 204 as the expected empty state rather than failing hydration.
2. SQLite could not translate ordering over a stored timestamp shape. The
   bounded candidate set is now filtered in SQLite and ordered in memory.
3. During a prolonged browser network outage, HTTP could recover before an
   existing WebSocket completed its reconnect. The supervisor now keeps
   controls disabled, detects that stuck channel, and safely recreates it.

The repository-wide line-ending helper also reported nine historical NuGet
lock files. They are outside the changed tree and were not rewritten. The
changed-file check passed. Security-pattern scanning reported three medium
documentation matches for prose about file-path containment; manual review
confirmed they are not executable file access and require no suppression.
