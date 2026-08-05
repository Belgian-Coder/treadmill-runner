# Real-Story Routing Benchmark

Date: 2026-08-03

## Subject

The frozen task was a real TreadmillRunner .NET domain story: validate expanded workout speed and incline targets against treadmill ranges and optional runner limits before arming. Each final workspace was scored with its visible xUnit suite, 15 withheld acceptance cases, and protected-baseline hash checks.

The shared stages were executed once, then copied before divergent review stages. Logical route cost includes every stage that route would require. The actual experiment total counts each executed shared stage once.

## Result

| Route | Quality | Hidden acceptance | Wall time | Plan usage cost | Versus direct | Total tokens |
|---|---:|---:|---:|---:|---:|---:|
| Sol medium direct | pass | 15/15 | 171.964 s | 14.404200 credits | 1.00× | 335,956 |
| Sol medium plan → Terra high → Sol medium final | pass | 15/15 | 445.334 s | 24.414465 credits | 1.69× | 788,395 |
| Sol medium plan → Luna high → Sol medium final | pass | 15/15 | 465.411 s | 17.165307 credits | 1.19× | 857,885 |
| Sol medium plan → Terra high → Luna low → Sol medium final | pass | 15/15 | 486.802 s | 23.569463 credits | 1.64× | 913,891 |
| Same preceding route → Sol high final | pass | 15/15 | 525.732 s | 24.235638 credits | 1.68× | 981,026 |

Credits are calculated from provider token receipts using the 2026-08-03 Codex rate card. They describe subscription consumption, not an invoice or API-dollar estimate. `output_tokens` already includes reasoning tokens.

Terra high and Luna high both passed the hidden suite before final review. The Luna-low stage preserved that result. From an identical post-Luna snapshot, both Sol-medium and Sol-high final reviewers changed no files. Sol-high therefore added 38.930 seconds and 0.666175 credits relative to Sol-medium without a measured quality gain.

The complete experiment used 68.628670 credits across nine unique executed stages. This is setup/research consumption, not the logical cost of any single route.

The apparent contradiction with cost-saving orchestration comes from the unconditional final Sol turn. Before final review, Sol-plan → Terra-high cost 13.464340 credits (6.5% below direct) and Sol-plan → Luna-high cost 8.594932 credits (40.3% below direct); both passed 15/15. Adding a fresh Sol review cost another 8.570375–10.950125 credits and repeated context already read by the planner and worker.

The cost-saving pattern is therefore conditional: one compact Sol plan, a cheaper implementation worker, deterministic acceptance, and a second Sol turn only after failed acceptance or for justified high-consequence review. Independent threads and different models do not share a prompt cache.

On a Pro 20x subscription these credits consume included capacity; they are not per-task dollar charges. Marginal cash cost is zero until the included allowance is exhausted. Purchased credits apply afterward at the account's displayed purchase price, and the fixed $200 subscription cannot be accurately allocated per task without before/after usage-dashboard readings.

## Decision

- Default to one direct Sol-medium agent for normal implementation work.
- Do not add a routine final reviewer when deterministic acceptance already passes.
- Use Sol-high as targeted escalation for ambiguity, failed acceptance, or justified consequence—not as an unconditional review stage.
- Keep direct Luna-max as an opt-in cost experiment for isolated low-risk work with deterministic validation.
- Do not promote a multi-stage route from this single repetition. Require three matched repetitions and apply the quality, fallback, credit, and wall-time gates.

The result applies to this well-specified .NET story. It does not establish equivalence for architecture, unclear requirements, security review, hardware protocol work, or other task classes.
## Hard full-stack story follow-up

A second benchmark used US-TR-008, a frozen production-shaped .NET/SQLite/REST/Blazor/Playwright feature. Unlike the earlier Core-only story, it required an EF migration, idempotent API, responsive UI, three screenshots, accessibility, Calendar integration, documentation, and workflow evidence.

| Route | Independent quality | Credits | API-equivalent USD | Wall time | Result |
|---|---:|---:|---:|---:|---|
| Direct Sol-medium | 95/100, ineligible | 640.067675 | $25.602707 | 2,782.701 s | Build/browser green; Calendar forecast stale after calendar mutations |
| Direct Terra-high | 75/100, ineligible | 59.089830 | $2.363593 | 1,288.202 s | Migration regression and missing Calendar summary |
| Direct Luna-high | 60/100, ineligible | 21.116880 | $0.844675 | 3,147.432 s | Calendar stale; final locked restore/publish drift; workflow packet over budget |
| Direct Terra-max | 60/100, ineligible | 284.776660 | $11.391066 | 4,715.269 s | Locked restore/browser publication failed with NU1004 |
| Direct Luna-max | 65/100, ineligible | 38.903586 | $1.556143 | 5,655.465 s | Cheap credits but 94.3 minutes; locked restore/browser publication failed |
| Sol-plan → Terra-high | 75/100, ineligible | 138.949015 | $5.557961 | 1,765.672 s | Browser gate failed |
| Sol-plan → Luna-high | 60/100, ineligible | 79.892753 | $3.195710 | 3,411.950 s | Locked restore/publish failed |
| Terra-high → Luna-low review | 75/100, ineligible | 59.748452 | $2.389938 | 1,383.081 s | Evidence-only review found no product issue |
| Terra-high → broad Terra-high review | 60/100, ineligible | 114.257300 | $4.570292 | 2,133.420 s | Migration and phone touch-target failures remained |
| Terra-high → broad Sol-medium review | 60/100, ineligible | 340.339780 | $13.613591 | 3,053.653 s | Final locked restore/browser publication failed |
| **Terra-high → targeted Luna-high repair** | **100/100, eligible** | **62.292776** | **$2.491711** | **2,059.733 s** | **Locked validation and Playwright 19/19 passed** |
| Terra-high → targeted Terra-medium repair | 85/100, ineligible | 88.426640 | $3.537066 | 1,867.464 s | Locked validation passed; 36px phone control remained |
| Terra-high → targeted Sol-medium repair | 100/100, eligible | 111.405480 | $4.456219 | 2,051.894 s | Same quality as Luna repair at 78.9% more credits |
| Sol-plan → Terra-high → broad Sol-medium review | 85/100, ineligible | 533.303040 | $21.332122 | 4,447.370 s | Deterministic gate passed; full browser suite failed |

The replacement benchmark excludes GPT-5.4 from routing because it is retiring and GPT-5.3-Codex is unavailable with the ChatGPT-authenticated CLI. The best supported route is Terra-high implementation, complete deterministic acceptance, then a fresh bounded Luna-high repair only when the gate provides a clear failure packet. That route independently passed at 100/100 for 62.292776 credits/$2.491711.

The same bounded failure packet given to Sol-medium also reached 100/100, but cost 111.405480 credits with essentially the same wall time. Targeted Terra-medium was not equivalent: it left a 36px phone target. Luna-high is therefore the cost-first repair candidate for clear non-safety failures; Sol remains the escalation for high-consequence or unresolved work.

Open-ended review was expensive and unreliable. Broad Terra review added 55.167470 credits and still failed migration/browser acceptance. Broad Sol review added 281.249950 credits and left package-lock drift. Sol-plan → Terra → Sol-review reached 533.303040 credits and still failed the full browser gate. The shared Sol plan alone added 56.809125 credits without improving either worker's score.

The provisional hard-story policy is therefore: keep implementation and planning in one persistent Terra-high task; stop after a green complete gate; otherwise dispatch the exact failure packet to a fresh bounded Luna-high repair and rerun the full gate. Use Sol-medium/high instead for safety, protocol, security, recovery, architecture, unresolved ambiguity, or after Luna fails. Require three matched repetitions before automatic promotion.

The largest avoidable costs were open-ended rediscovery and workflow closeout. The bounded repair prompt prohibited lifecycle regeneration and supplied exact failures; Luna then added only 3.202946 credits. The lean target is one story, one persistent implementation task, one complete gate, one machine-generated failure packet, an optional bounded repair, and one final evidence report.

The legacy numeric scorer still includes candidate-authored test/source heuristics, so hard eligibility is anchored to the actual locked validation and browser commands. A separate route-blind inspection confirmed the package-lock, migration, Calendar, touch-target, and browser findings. Future protocol should inject evaluator-owned behavior tests.

Detailed disposable receipts, independent evaluations, prompts, and reproduction scripts remain under the operator's separate benchmark workspace and are not committed with the product.
