# Voice Reference Import Fix

Date: 2026-11-19
Status: Ready for production deployment

## Reported Failure

Importing `地狱开局三岁怪童_第04集_画布载荷.json` failed with:

```text
VOICE_REF_MISMATCH（段9）
```

The expected voice list contained only:

```text
audio-1794641370261-3bdq6
```

The transient imported list also contained the previously generated Hu Zi
voice:

```text
audio-1794652390089-hj04q
```

## Root Cause

This was an importer bug, not invalid JSON.

- Segment 9 explicitly names Cheng Xi as the OS speaker.
- The visual description mentions Hu Zi because Cheng Xi looks at his back.
- The OS speaker inference continued scanning the visual description even
  though the dialogue already had an explicit speaker.
- The importer also attached an already existing project voice whenever a
  referenced character had one, even when that character did not speak in the
  current segment.

Together these rules attached Hu Zi's existing audio to a Cheng Xi-only
segment. The final import contract correctly rejected the inconsistent
`voiceRefs` and rolled the import back.

## Fix

- Explicit dialogue speakers are now authoritative; OS inference only runs
  when no speaker is labeled.
- A voice node is attached to a video only when that character actually speaks
  in the segment.

## Verification

- Targeted regression tests: `2 passed`.
- Full `test_import_feed`: `65 passed`.
- Full `test_canvas_feed_negative_contract`: `32 passed`.
- Python compile check: passed.
- Read-only preview against the same project and uploaded JSON:
  - `plan_ok: true`
  - `plan_errors: []`
  - Segment 9 `voiceRefs`: Cheng Xi only.
  - Segment 9 audio edges: Cheng Xi only.

No paid image, audio or video generation was submitted.
