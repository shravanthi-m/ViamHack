---
name: demo1
description: Candidate fixed-layout signature pour with measured image translation and automatic per-object gripper force.
---

# Demo 1 — v2

Status: **software candidate; measured vision profiles and physical rehearsal pending**.

Retains the v1 taught coconut/pitcher pose tracks and original cup/return locations.
Use [vision setup](../../../../docs/vision_demo.md) to build local image-translation
profiles from independent physical measurements; never substitute the old global
homography, guessed offsets or detector boxes. Keep source orientation/height and
camera mounting fixed. Motion is bounded to the existing replay limits.

`primitives/replay_vision.py` matches two patches, rejects inconsistent or ambiguous
images, and supplies measured XY translation to the existing taught replay adapter.
`ufactory_atomic` closure uses explicit object settings. Configuration opts in to
vision only when both source profiles exist and pass offline checks. Full scene,
clearance, intended-object, release and outcome gates remain supervised.

The preparation includes same-image checks on candidate patches in the original
station photographs and synthetic image/adapter tests. Neither establishes actual
localization accuracy or successful robot manipulation. No new physical trial is
claimed. Promotion still requires the same frozen revision and setup passing two
consecutive observed full pour/return trials. Preserve the v1 failure record.
