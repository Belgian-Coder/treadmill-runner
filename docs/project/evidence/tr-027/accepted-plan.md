# Accepted plan — TR-027

The owner approved direct implementation without intermediate questions. The accepted scope hides template-generated definitions from the standalone workout library, provides one remembered application-wide runner selector, derives the complete plan calendar from its schedule, and persists only previewed sparse moves, skips, restores, and extra repeat occurrences.

The later clarification is included: a completed but unsatisfactory run may be repeated on a full calendar while either keeping later dates or shifting later incomplete plan sessions. Collisions are disclosed and never overwrite an entry. Stopped, interrupted, and faulted attempts remain incomplete and are rescheduled. No calendar operation prepares a workout or sends a treadmill command.
