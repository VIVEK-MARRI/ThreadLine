# Stage 35 Final Report — Complete Landing Page Replacement

ThreadLine public marketing experience rebuilt from scratch as a premium,
immersive, product-true story. No commit, no push, no tag per instructions.

## 1. Complete landing-page replacement

Replaced the entire public landing page at `/`. Nothing was patched:
`LandingPage.tsx`, `landingContent.ts`, `landing.css`, `ThreadDiagram.tsx`,
`MemoryTransformation.tsx`, `EvidenceChain.tsx`, `AskIllustration.tsx`,
`FollowThreadDiagram.tsx` were fully rewritten; `EntityMap.tsx` and
`IntelligenceSignal.tsx` are new; `useLandingAnimations.ts` gained
`usePrefersReducedMotion` and `useMediaQuery`; `landing.test.tsx` and
`e2e/landing.spec.ts` were rewritten/extended; `index.html` gained OG tags.
Product `tokens.css`, AppShell, all `/app` routes, backend, and CI are untouched.

## 2. Product positioning

"Your organisation remembers what happened." ThreadLine turns meetings and
ongoing work into connected organisational memory — understand what changed,
trace it back to evidence, know where follow-up exists. Copy uses the
product verbs (remember, connect, trace, investigate, observe, detect,
history, change, evidence, follow) and claims only real capabilities.

## 3. Visual direction

Editorial magazine + premium software + technical architecture visualisation
with cinematic motion. Rhythm: ivory hero → ivory problem → parchment
transformation → ivory entity map → deep-navy organisation intelligence →
ivory proactive scan → ivory evidence → ivory ask → deep-navy follow-the-thread
centerpiece → ivory security → copper-washed final CTA → navy footer.

## 4. Color system

Landing-scoped vars in `landing.css` (product `tokens.css` untouched):
warm ivory `#f6f1e7`, parchment `#ede4d0`, deep ink `#211d16`,
navy `#0c1828`/`#101f33`, petrol teal `#22705f`, soft teal `#9fc4b5`,
copper `#a06a1f` with soft highlight `#e6c98f`. No purple gradients, no
rainbow, no neon. Secondary text `#6b6357` holds 4.5:1 on ivory (axe-verified).

## 5. Typography

Token-based hierarchy: clamp hero (2.3–3.4rem, controlled), 1.75rem section
heads, 1rem/1.65 body at ≤65ch, uppercase meta labels used once per section.
No giant display type, no eyebrow-label spam.

## 6. Glassmorphism strategy

Glass (12px blur, translucent ivory, hairline border, restrained shadow) is a
material for four hero/product objects only: hero thread panel, structured
memory panel, entity identity card, ask answer. Everything else is solid
paper/card/navy. Never wallpaper.

## 7. Hero

Split editorial hero: headline + supporting message + Open ThreadLine
(/app) + Explore the thread (#thread). Visual: layered glass surface holding
the ThreadDiagram — Meeting → Observation → Entity → Memory → Change →
Intelligence → Evidence with staged load (depth settles, structure appears,
edges draw in sequence, nodes resolve, evidence node in copper). Settles to a
barely-perceptible halo shimmer. Mobile gets a dedicated vertical thread
variant with readable beside-node labels.

## 8. SVG system

Shared vocabulary (1.5–2px lines, 6–7px nodes, 12.5px labels, teal/copper):
ThreadDiagram, MemoryTransformation, EntityMap, EvidenceChain,
IntelligenceSignal (new: stable node field, scan sweep, one emerging copper
signal threaded to entity + evidence), AskIllustration (staged
question → searching → answer → cited evidence, no fake typing),
FollowThreadDiagram (8 scroll-activated stages with progress line).

## 9. Animation system

CSS keyframes + transitions + IntersectionObserver only. No Three.js, GSAP,
Lottie, or new dependencies. Text entrances are transform-only so contrast is
final from frame one (axe-deterministic); opacity staging is reserved for
below-fold choreography and decorative layers.

## 10. Scroll choreography

Section reveals, fragment chips → connecting thread, staged memory fields,
entity facets, org flow steps, scan sweep, evidence chain, ask sequence, and
follow-thread stage activation with a progress fill. Staggered, never
simultaneous; no extreme parallax.

## 11. Product storytelling

Problem (fragmented → connected) → meeting-to-memory (glass transformation,
illustrative example marked) → entity workspace → organisation intelligence
(association ≠ dependency ≠ causation) → proactive scan → evidence tracing →
ask → follow-the-thread climax → security → CTA. One continuous thread.

## 12. Real screenshots

Deliberately none embedded: crafted SVG/HTML compositions carry every
section instead of app-screen bitmaps, keeping the bundle light (landing
chunk 28.22 kB, 7.41 kB gzip) and avoiding stale captures. Existing validated
app screenshots remain in e2e/screenshots for the /app routes.

## 13. Responsive implementation

1440×900, 1024×768, 390×844 verified in-browser: hero splits → stacks with
vertical thread; memory/entity grids collapse; flow rows wrap; CTAs go
full-width; sticky entity card unpins; zero horizontal overflow at all three
(e2e-asserted).

## 14. Accessibility

One h1, logical h2s, section aria-labelledby, skip link, real links/buttons,
visible focus rings, Escape-closing mobile menu with focus return,
keyboard-operable thread/evidence nodes, labelled SVGs, contrast-fixed
palette, full reduced-motion support. Axe passes at all three viewports.

## 15. Performance

No new dependencies, no images/video/particles, SVG+CSS only, landing
code-split (28.22 kB / 7.41 kB gzip). No massive hidden DOM.

## 16. SEO

index.html: title, description, og:title, og:description, og:type (no
invented domain). LandingPage additionally sets OG tags at runtime with
cleanup on unmount.

## 17. CTA/auth routing

Open ThreadLine → `/app` → unauthenticated guard → `/login` (browser-verified
and unit-tested via guard redirect to Sign in). Explore the thread → #thread.
`/app/*`, `/login`, `/setup` untouched and passing.

## 18. Tests added

`landing.test.tsx`: 39 tests (was ~30 in the old suite, all rewritten):
render/main/footer/skip-link (4), hero incl. 7 nodes (6), nav + mobile menu
incl. Escape/focus (5), responsive compact/wide thread (2), all 10 sections
(11), hover/focus interactions (4), title + OG metadata (2), link integrity +
CTA routing + no-emoji (4), reduced motion (1).

## 19. Browser validation

Full Playwright suite: 51/51 passed (chromium), including landing at 3
viewports (loads, hero, overflow, axe ×3), CTA→auth, SVG animation presence,
mobile menu, reduced motion, console/network cleanliness, anchor scrolling,
evidence hover, ask reveal, thread activation.

## 20. Screenshots captured

landing-hero-desktop, landing-meeting-memory, landing-intelligence,
landing-evidence, landing-follow-thread, landing-mobile, landing-full-desktop
— all inspected (see §Visual QA notes in working log: hero premium and
legible; dark bands cinematic; glass restrained; mobile vertical thread
readable; evidence chain clean).

## 21. Console/network results

`expectNoSevereBrowserIssues` green across landing tests: zero page errors,
zero failed requests, zero console errors.

## 22. Exact test counts

- `npx tsc --noEmit`: clean (exit 0)
- `npm test`: 26 files, 216 passed (landing file: 39)
- `npm run build`: clean, landing chunk 28.22 kB (7.41 kB gzip)
- `npm run test:e2e`: 51 passed, 0 failed
- Backend `python -m pytest -q`: 1001 passed, 4 skipped, 1 failed —
  `test_stage_24_e2e.py::test_real_full_stack_tenant_acceptance`
  (semantic.json cross-tenant filesystem assertion; fails identically in
  isolation; zero backend files modified in this stage — pre-existing and
  unrelated to the landing replacement).

## 23. Typecheck/build results

See §22. Typecheck clean, build clean, no new dependencies added
(`package.json` untouched).

## 24. Known limitations

- The pre-existing backend e2e failure (§22) is out of scope for this stage.
- The worktree also contains uncommitted prior-stage files (AGENTS.md,
  router/Logo/main/routing-test modifications, STAGE_35A_SETUP_REPORT.md);
  this stage's files are the landing feature, index.html OG tags,
  e2e/landing.spec.ts, and this report. Nothing was committed per instructions.
- Real app screenshots intentionally not embedded (§12).

## 25. Intentionally unclaimed capabilities

No real-time monitoring, predictions, notifications, alert delivery, accuracy
percentages, customer logos, testimonials, pricing, metrics, or causation
beyond observed association. Proactive section carries an explicit caveat;
all example content is marked illustrative.

## 26. Files changed

- Rewritten: `frontend/src/features/landing/{LandingPage,landingContent,landing,
  ThreadDiagram,MemoryTransformation,EvidenceChain,AskIllustration,
  FollowThreadDiagram,landing.test}.tsx|.ts|.css`, `frontend/index.html`
- New: `frontend/src/features/landing/{EntityMap,IntelligenceSignal}.tsx`,
  `STAGE_35_FINAL_REPORT.md` (this file),
  `frontend/e2e/screenshots/landing-{meeting-memory,intelligence,evidence,
  follow-thread}.png` (captured artifacts)
- Extended: `frontend/src/features/landing/useLandingAnimations.ts`,
  `frontend/e2e/landing.spec.ts`
- Untouched: backend, AppShell, `/app` routes, CI, deployment config, deps.
