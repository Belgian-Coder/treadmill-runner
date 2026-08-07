# Resolved failure summary — TR-025

- A bare cross-profile 404 was rewritten by the SPA fallback as a misleading content-type error. The endpoint now returns an explicit safe 403 problem response and the integration test covers it.
- The initial schedule UI could label a non-training weekday as the first training date. The domain now rejects that state and the UI defaults to the next selected weekday.
- Generated migration formatting and a stale documentation link failed the first local quality packet. The project formatter normalized the migration and the obsolete link was removed.
- A raw quality report was initially written under public evidence and correctly rejected because it contained local source paths. Raw output now stays in ignored validation storage; this packet contains only sanitized summaries.
- The first full browser sweep passed 53 of 55 scenarios. One gallery expectation still used **Cancel**, and multiple accumulated long runner names overflowed the iPhone calendar. The expectation now uses **Keep for later**, profile pills are bounded with ellipsis, and the final 15-case affected-screen matrix passed.
