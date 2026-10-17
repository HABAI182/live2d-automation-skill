# Compatibility and verification boundaries

Status checked 2026-10-17 against the current source and [Live2D announcement](https://www.live2d.com/en/information/cubism-5_4-alpha/).

| Component | Verified scope | Limitation |
|---|---|---|
| Native edit adapter | Windows, Cubism 5.4.00 alpha2 / API 1.1.0, English UI, Modeling, Allow/Edit | Pilot; historical real Editor evidence dated 2026-10-15 |
| Alpha2 deadline | Available through 2026-10-18 in JST; writes blocked from 2026-10-19 00:00 JST | Use the vendor timezone, not the computer timezone; no extension applied |
| Cubism 5.3.04 / API 1.0.1 | Historical basic connection/parameter evidence | Not a substitute for the Alpha2 structural adapter |
| Successor versions | Not adapted or accepted | Unknown Editor/API fails closed; re-probe and independently verify before support |
| Unit/static/package CI | Windows/Linux, Python 3.11 and 3.14 | Protocol fixtures do not prove live Editor, UI focus, SDK pixels or native desktop behavior |
| File tools / SDK verifier | Independent of Alpha2 native edit expiry | SDK/Core/Node/browser remain separately supplied, licensed and tested prerequisites |

The 2026-10-17 regression corrects the former UTC+8 date calculation to JST, eliminating a one-hour write window after the vendor deadline. Boundary tests cover the last second and first blocked second; UID, fresh snapshot, explicit confirmation, backup, locks and recovery protections remain in place.

Actions use immutable commit pins and read-only repository permission. `requirements-ci.txt` pins CI tools; bounded runtime dependency ranges stay in `pyproject.toml`, so it is not a full transitive lock. No CI step uploads character assets, evidence screenshots or SDK binaries. `test_real_editor` stays opt-in. Alpha data licensing/compatibility restrictions in the vendor announcement remain separate from tool-code licensing.
