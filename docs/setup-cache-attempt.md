# Local cache-only setup attempt

On 30 October 2026, one owner-approved, first-party cache-only setup attempt ran locally. It started at 14:34:00 UTC and ended at 14:34:16 UTC: 15.96 seconds, with parent exit code 1. The case-sensitive filesystem probe, checks of the held caches and package archives, and checks of isolated environments and cached dependencies completed. The driver reached `native_build`, then stopped cooperatively with a `TimeoutError` when its local work-window guard closed. The exact guard field was not retained. This does not establish a compiler failure or a 30-minute wall-time expiry.

The native-engine build did not complete. Native installation, import, and review were not reached. There is no clean-install or clean-machine pass. Earlier feed, wrapper, and vendoring failures remain historical, and the manual fallback remains a separate recorded path; this attempt does not erase or replace either. The attempt is consumed, with no automatic retry or source bypass.

A later source-only provenance check matched the held inputs and setup-source pins to their recorded manifest and authorization. It did not run another build or setup, and it did not establish a new local work window.

The cache inputs and authorization records remain outside this source tree; their names, contents, and private locations are not reproduced here. This note makes no claim about redistribution rights, hosted setup, or release acceptance.
