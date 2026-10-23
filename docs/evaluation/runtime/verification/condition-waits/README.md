# Event-sensitive general conditions

`Tab.wait` now wakes on DOM, input, load and visibility signals. A 100 ms fallback preserves globals/time checks; asynchronous predicates do not overlap, and the original absolute deadline still bounds them. Resources activate only for a wait and are cleared on match, expiry, pagehide, cancellation or timeout.

The fixed document-start bootstrap captures the APIs needed to evaluate explicitly requested page predicates despite later page overrides. Snapshot/action code remains isolated. CSP does not require eval/Function construction.

The isolated patch passed 50 focused checks, including 10 new public regressions and unchanged wait/navigation/form/wake/runtime/cleanup tests. The cold WindowServer-helper timing failure and unchanged full-suite-order rerun are retained in the lane receipt. The combined wait/transport build passed 25 focused checks; full regression and official latency measurement are separate gates.
