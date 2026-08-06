# Sanitized failure summary — TR-024

- The first persistence build exposed program-provenance configuration attached to the wrong EF entity. The mapping was moved to the program revision and the Release build returned to zero warnings/errors.
- The schema inventory and transactional-restore tests used a fixed historical migration count. They now compare every discovered migration with the applied set and explicitly check the new installation table.
- The E2E migration helper redirected verbose output without safely handling inherited pipe handles. It now inherits the test console streams, eliminating the validation deadlock while preserving migration failures.
- Two initial browser assertions used ambiguous accessibility selectors. They were narrowed without changing application behavior, then the end-to-end flow passed.
- The first long-plan screenshot rendered phases as one narrow vertical column. An explicit responsive phase grid replaced the implicit details layout; the refreshed image is compact and readable.

No production data, credentials, device identities, or treadmill commands were involved in these failures.
