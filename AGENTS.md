# ThreadLine — agent working notes

## Instruction Precedence

1. Actual repository behavior and backend contracts
2. ThreadLine product semantics and architecture
3. ThreadLine design system and project-wide rules
4. Task-specific implementation brief
5. Installed design/testing skills
6. Generic framework conventions

Skills are advisory implementation/design knowledge. They must NOT override ThreadLine's product semantics, backend contracts, design system, accessibility requirements, security rules, or architectural constraints.

## Backend (`app/`)

- FastAPI + SQLite + Pydantic. Run the full suite:

  `python -m pytest` (everything, incl. security contract tests)

- Fast special-case runs that gate a stage:

  `python -m pytest tests/test_stage_24_security.py tests/test_frontend_contracts.py -q`

- Boot for manual QA:

  `python -m uvicorn app.main:app --port 8000`

- All API paths are versioned under `/api/v1`. Remote first-run is open
  (`/api/v1/auth/bootstrap`); after an owner exists the server is locked.

## Frontend (`frontend/`)
- Vite + React 19 + TypeScript. All commands run inside `frontend/`.
  - Dev server: `npm run dev` (proxies `/api` and `/health` to backend on port 8000)
  - Typecheck: `npm run typecheck` (alias `npx tsc --noEmit`)
  - Unit/route tests: `npm test` (vitest, jsdom)
  - Production build: `npm run build` (typecheck + `vite build`)
- Tests: `src/**/*.test.{ts,tsx}`. No production mock data; integration-style
  route tests use `src/test/harness.tsx` to stub fetch against real backend
  contracts.
- The API client in `src/api/client.ts` is the only place that calls fetch.

## Process conventions
- Verify a stage with the typecheck + `npm test` / `pytest` that gated it, then
  write a `STAGE_NN_FINAL_REPORT.md` at the repo root, commit, and push only
  when the user asks.
- Never stage or commit unrelated files.

## ThreadLine Product Truth

ThreadLine is currently:
- a modular monolith
- FastAPI backend
- React + TypeScript frontend
- durable SQLite source of truth
- tenant-scoped repository architecture
- durable background jobs
- process-local worker
- deterministic domain intelligence
- semantic retrieval
- natural-language query
- evidence-backed intelligence
- proactive intelligence scanning
- single-node deployment

Do not describe ThreadLine as microservices, cloud-native distributed system, horizontally scalable, Kubernetes-based, Redis-based, Kafka-based, or graph-database based unless those things actually exist in the repository later.

## Source-of-Truth Rule

The repository is the source of truth.
Agents must inspect existing implementation before proposing or inventing features.

Never invent:
- backend fields
- endpoints
- API responses
- entity types
- intelligence semantics
- causal relationships
- evidence
- metrics
- permissions
- product capabilities

Semantic similarity does not establish identity, dependency, or causation.
LLM output does not become organisational truth merely because a model generated it.

## Design System Rules

ThreadLine should feel:
- calm
- intelligent
- precise
- warm
- editorial
- trustworthy
- human
- technically credible

Prefer:
- strong typography
- deliberate whitespace
- clear hierarchy
- restrained color
- subtle borders
- controlled elevation
- custom SVG
- meaningful diagrams
- purposeful interaction
- refined motion

Avoid:
- generic AI SaaS styling
- excessive gradients
- purple/blue AI gradients
- glassmorphism
- decorative blobs
- neon effects
- fake dashboard decoration
- excessive rounded containers
- stock AI illustrations
- generic robot imagery
- gradient headlines
- excessive eyebrow labels
- arrow-suffixed CTA text
- decorative metric cards
- unnecessary animation

## No Emojis

ThreadLine does not use emojis in product UI, product copy, documentation,
README files, marketing pages, or generated interface content unless the user
explicitly requests emojis for a specific task.

This applies especially to:

- README
- AGENTS.md
- design documentation
- product copy
- UI text
- landing-page content

Use SVG, typography, CSS, layout, diagrams, and normal text for visual
communication instead.

Do not use emoji characters as decorative bullets or headings.

## Typography Rules

- use a consistent type hierarchy
- body text should remain comfortably readable
- avoid overly wide text blocks
- target approximately 65–75 characters for long-form reading text
- use restrained heading sizes
- do not use enormous hero typography merely for visual impact

Use 1.25 / 1.333 as the default typographic-ratio reference when constructing scales. These are guidelines, not absolute mathematical constraints. Deliberate exceptions are allowed when they improve hierarchy or responsiveness.

## Spacing Rules

Use an 8px base spacing rhythm for new design work.
Prefer values derived from the system. Do not introduce arbitrary spacing everywhere.
Existing ThreadLine spacing tokens remain authoritative. If the existing implementation already uses a tokenized system, reuse it instead of creating a parallel spacing scale.

## Accessibility Rules

Permanent minimum:
- WCAG-oriented contrast
- 4.5:1 minimum for normal text
- visible keyboard focus
- semantic HTML
- real links/buttons
- accessible forms
- meaningful headings
- color is never the sole carrier of meaning
- reduced-motion support where animation exists

Use the existing ThreadLine accessibility standards. Do not add ARIA just to satisfy a checker when native semantics are sufficient.

## Motion Rules

Motion is allowed and encouraged when it communicates: connection, chronology, evidence, discovery, transformation, continuity.

Good motion:
- SVG line drawing
- node reveal
- subtle section reveal
- small scale/opacity changes
- evidence tracing
- intentional hover state
- restrained scroll choreography

Bad motion:
- constant floating
- excessive bounce
- animated gradients
- particle fields
- cursor trails
- aggressive parallax
- endless pulsing
- animation for decoration alone

Every animation should have a reason. Respect prefers-reduced-motion.

## Landing-Page-Specific Visual Principle

The future ThreadLine landing page should use: HIGH CRAFT + LOW NOISE.

The page may contain:
- custom SVG
- subtle animation
- product illustrations
- real screenshots
- visual storytelling
- diagrams
- timeline motifs

But visual effects should explain ThreadLine.
The recurring visual metaphor is:
Meeting → Observation → Entity → Memory → Change → Intelligence → Evidence → Follow-up

The thread itself can become part of ThreadLine's visual identity.

## Anti-AI-Slop Rule

The project must avoid looking like a generic AI-generated website.

Reject designs that rely on:
- giant gradients
- floating cards everywhere
- fake glass panels
- generic "AI" blobs
- stock corporate photography
- random 3D objects
- excessive pills
- repeated card grids
- huge meaningless statistics
- buzzword-heavy copy
- visual effects without product meaning

Prefer:
- specificity
- restraint
- visual continuity
- editorial composition
- authentic product UI
- custom diagrams
- real product concepts
- meaningful interaction

## UX Pro Max Role

UI/UX Pro Max may be used for:
- design exploration
- typography
- layout
- visual hierarchy
- interaction ideas
- UX heuristics
- responsive recommendations

But existing ThreadLine design tokens and product semantics remain authoritative.

## Frontend-Design Role

The frontend-design skill may be used for:
- frontend composition
- visual craftsmanship
- anti-template design
- subject-specific visual language
- component design
- polished UI implementation

It must not override:
- ThreadLine architecture
- existing design tokens
- accessibility rules
- product semantics
- backend contracts

## Webapp-Testing Role

The webapp-testing skill may be used for:
- browser automation
- E2E testing
- interaction testing
- responsive checks
- accessibility checks
- console/network checks
- screenshots

Do not use browser tests to justify fabricated product behavior. Tests must verify actual behavior.

## Real Data Rule

Production UI must not use permanent fake data.
Illustrative content is allowed in clearly identified marketing contexts, such as a landing page.
When examples are illustrative, do not make them appear to be real tenant data, real customer metrics, or real evidence.

## Security Rule

Frontend permissions are display hints only. Backend authorization is authoritative.
Never implement a frontend-only security boundary.

Never log:
- passwords
- session tokens
- API keys
- authorization headers
- private transcripts
- sensitive evidence

## Performance Rule

Prefer:
- existing dependencies
- CSS
- SVG
- lightweight browser APIs
- existing React Query architecture

Avoid adding large libraries merely for visual effects.
Do not add Three.js, GSAP, Lottie, or particle libraries unless there is a strong repository-specific reason and the user explicitly approves scope expansion.

## Code Quality Rule

Prefer:
- small cohesive components
- clear responsibility boundaries
- typed interfaces
- existing abstractions
- reuse over duplication

Do not perform massive refactors without a clear reason. Do not solve cosmetic issues by rewriting working architecture.

## Testing Rule

Every meaningful new feature should have appropriate coverage.

Prefer:
- unit tests for deterministic logic
- integration tests for API/data flow
- Playwright for critical user journeys
- accessibility testing where applicable

Never inflate test counts with meaningless assertions.

## Documentation Rule

- Root README: concise public-facing product/engineering introduction.
- AGENTS.md: project-wide implementation and design rules.
- DEPLOYMENT.md: deployment/operator instructions.
- Stage reports: historical implementation records.

Do not turn README into a stage history dump.

## Git Rule

Do not commit, push, or tag unless the user explicitly requests it.
Never claim a commit or push happened unless it actually happened.