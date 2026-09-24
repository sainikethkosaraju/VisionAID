# Caregiver App & Facility Dashboard

> **Status: PENDING (Phase 9).** Not started by design — the backend contract (API.md) and incident engine come first. Nothing in `frontend/` or `mobile/` is functional yet.

## Platform decision

| Surface | Choice | Why |
|---|---|---|
| Caregiver mobile app | **React Native (Expo) + TypeScript** | Life-safety alerts need native capabilities a PWA can't guarantee: iOS *Critical Alerts* (entitlement, bypasses silent mode), Android full-screen intents / high-priority FCM, reliable background delivery |
| Facility dashboard | **React + TypeScript (Vite)**, responsive | Large-screen mission-control view, floor map, analytics |
| Shared | Generated TypeScript API client from OpenAPI; shared design tokens | One contract, one visual language |

## Information architecture
`HOME · INCIDENTS · RESIDENTS · DEVICES · ANALYTICS · PROFILE`

- **Home — "Is everyone safe?"**: one sentence answer backed by `/facility/overview.all_clear` + reasons; active critical incidents first; cameras online/degraded/offline; residents monitored; today's alerts and response times.
- **Incident**: severity, room/floor/camera, time, confidence (labelled *alert score*, not probability), resident if matched, timeline, evidence (clinical roles), current responder, escalation state; actions **Acknowledge · Responding · Resolve · False positive** — large, thumb-reachable, confirm on destructive action.
- **Devices**: status, Wi-Fi RSSI, last heartbeat, firmware/model, faults; register/rename/move/test/restart/rotate.
- **Map (Phase 10+)**: uploaded floor plan, draggable cameras/rooms, live incident state.

## Design system
- Semantic colours: RED critical · ORANGE warning · BLUE info/technology · GREEN safe/resolved · NAVY system/trust · off-white content · greys secondary. State is **never colour-only**: icon + label + shape.
- **Brand primary "Adwith red": exact value needed.** It could not be identified as a standard named colour. Until supplied, tokens reference `--brand-primary` with a placeholder, and brand red is kept distinct from the semantic *critical* red so an alert never looks like decoration.
- Typography: large numerals for counts/timers; minimum 16 px body; 44×44 pt touch targets (48 dp Android).
- Light and dark mode (night shifts); WCAG 2.2 AA contrast minimum; screen-reader labels on every state.
- Motion only to signal change (new incident), respecting reduced-motion.

## Acceptance criteria for Phase 9
From a push notification, a caregiver can acknowledge in ≤ 2 taps and ≤ 3 s; home screen answers "is everyone safe?" in one glance; every button is wired to a real endpoint or visibly labelled PENDING.
