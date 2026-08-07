# Sanitized failure summary — TR-026

- The first focused browser invocation used a previously published test host, so it displayed the earlier UI. The Release gateway was republished before the final browser matrix.
- One focused test selector used an outdated method name and selected no test. The corrected exact test selector passed and the empty invocation is not counted as validation evidence.
- Initial full-page modal captures included content outside the fixed viewport and made backdrop coverage misleading. Modal showcase captures now use the actual viewport; desktop and iPhone overflow checks pass.

No product safety defect, credential exposure, persistence migration, treadmill command, or hardware interaction occurred.
