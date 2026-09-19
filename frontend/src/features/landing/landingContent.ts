/* Landing-page copy in one module for editorial consistency.
 * Every line is intentional, grounded in the real product, and free of
 * invented capabilities, metrics, or proof. Zero emojis. */

export const LANDING = {
  meta: {
    title: "ThreadLine — Organisational memory from your meetings",
    description:
      "ThreadLine turns meetings and ongoing work into connected organisational memory — understand what changed, trace it back to evidence, and know where follow-up exists.",
    ogType: "website",
  },

  nav: {
    brand: "ThreadLine",
    links: [
      { label: "How it works", href: "#how-it-works" },
      { label: "Intelligence", href: "#intelligence" },
      { label: "Evidence", href: "#evidence" },
      { label: "Security", href: "#security" },
    ] as const,
    cta: "Open ThreadLine",
    ctaHref: "/app",
    mobileMenuLabel: "Menu",
    mobileCloseLabel: "Close menu",
    skipLink: "Skip to content",
  },

  hero: {
    headline: "Your organisation remembers what happened.",
    body: "ThreadLine turns meetings and ongoing work into connected organisational memory — so you can understand what changed, trace it back to evidence, and know where follow-up exists.",
    primaryCta: "Open ThreadLine",
    primaryCtaHref: "/app",
    secondaryCta: "Explore the thread",
    secondaryCtaHref: "#thread",
    visualLabel:
      "ThreadLine information flow: Meeting to Observation to Entity to Memory to Change to Intelligence to Evidence",
  },

  problem: {
    id: "problem",
    label: "The problem",
    headline: "Important context disappears between meetings.",
    body: "Decisions, dependencies, issues, and follow-ups scatter across transcripts, notes, and individual memory. Weeks later, the thread that connects them is gone.",
    fragments: [
      "A decision is made",
      "A dependency appears",
      "An issue stays open",
      "A follow-up is promised",
      "A conversation moves on",
    ],
    resolution:
      "ThreadLine preserves the thread — every observation stays connected to the entity, the meeting, and the evidence behind it.",
  },

  meetingToMemory: {
    id: "how-it-works",
    label: "How it works",
    headline: "From conversation to structured memory.",
    body: "ThreadLine extracts structured observations from meeting conversation and resolves them to canonical entities — deterministically, with sources attached.",
    conversationLabel: "Meeting conversation",
    conversation: [
      { speaker: "PM", text: "We are still blocked on the payment gateway." },
      { speaker: "Eng", text: "The launch depends on it." },
      { speaker: "PM", text: "We will revisit this next week." },
    ],
    extractionLabel: "Structured memory",
    extraction: [
      { field: "Entity", value: "Payment Gateway" },
      { field: "State", value: "Blocked" },
      { field: "Dependency", value: "Launch depends on Payment Gateway" },
      { field: "Source", value: "Weekly sync, 14 Jan" },
      { field: "Evidence", value: "“We are still blocked on the payment gateway.”" },
    ],
    illustrativeNote: "Illustrative example",
  },

  entityIntelligence: {
    id: "product",
    label: "Entity intelligence",
    headline: "Every entity becomes a persistent thread.",
    body: "An entity is not a static record. It accumulates state, history, dependencies, evidence, and recommended actions as organisational understanding evolves.",
    entity: {
      name: "Payment Gateway",
      state: "Blocked",
      illustrativeNote: "Illustrative example",
      facets: [
        { label: "Current state", detail: "Blocked since 14 Jan" },
        { label: "History", detail: "Observations across 4 meetings" },
        { label: "Meetings", detail: "Weekly sync, Architecture review" },
        { label: "Dependencies", detail: "Launch, Billing API" },
        { label: "Impact", detail: "Launch held until resolved" },
        { label: "Attention", detail: "Needs review this week" },
        { label: "Evidence", detail: "3 cited source excerpts" },
        { label: "Actions", detail: "Escalate to vendor" },
      ],
    },
  },

  orgIntelligence: {
    id: "intelligence",
    label: "Organisation intelligence",
    headline: "Intelligence from evidence, not invention.",
    body: "ThreadLine derives organisational intelligence deterministically. Observed association is not dependency. Dependency is not automatically causation. Every signal traces back to evidence.",
    flow: [
      { label: "Change", detail: "Transitions detected in entity state" },
      { label: "Entity", detail: "Mentions resolved to canonical identity" },
      { label: "Relationship", detail: "Associations examined, never assumed" },
      { label: "Impact", detail: "Cross-entity effects assessed" },
      { label: "Evidence", detail: "Every signal cites its sources" },
    ],
  },

  proactive: {
    id: "proactive",
    label: "Proactive intelligence",
    headline: "Signals surface when they become relevant.",
    body: "ThreadLine periodically re-evaluates organisational state and identifies newly relevant intelligence signals. Known signals settle. New signals become gently prominent — always with evidence attached.",
    flow: [
      "Organisation state",
      "Periodic scan",
      "Signals evaluated",
      "Newly relevant",
      "Evidence cited",
    ],
    caveat:
      "Proactive intelligence is a scheduled re-scan of existing evidence — not real-time monitoring, not predictions, and never a fabricated discovery.",
    scanLabel:
      "Proactive scan illustration: a quiet field of stable organisation nodes with one newly relevant signal emerging",
  },

  evidence: {
    id: "evidence",
    label: "Evidence",
    headline: "Trace the signal back to what happened.",
    body: "Hover or focus any step to follow its thread. Every intelligence signal can be traced through the entity and the meeting to the exact source excerpt that produced it.",
    chain: [
      { label: "Intelligence signal", detail: "Blocked dependency detected" },
      { label: "Entity", detail: "Payment Gateway" },
      { label: "Meeting", detail: "Weekly sync, 14 Jan" },
      { label: "Source excerpt", detail: "“We are still blocked on the payment gateway.”" },
    ],
  },

  ask: {
    id: "ask",
    label: "Ask ThreadLine",
    headline: "Ask about your organisation in plain language.",
    body: "ThreadLine retrieves evidence, scores it, and builds a grounded answer. When evidence is insufficient, it says so instead of guessing.",
    illustrativeNote: "Illustrative example",
    questionLabel: "Question",
    question: "What happened to the payment gateway?",
    searchingLabel: "Searching organisational memory…",
    answerLabel: "Answer",
    answer:
      "The payment gateway is currently blocked. It was first raised in the architecture review on 7 Jan and confirmed as blocked in the weekly sync on 14 Jan. The launch depends on its resolution.",
    evidenceLabel: "Cited evidence",
    evidence: [
      { type: "Entity", detail: "Payment Gateway" },
      { type: "Meeting", detail: "Weekly sync, 14 Jan" },
      { type: "Source", detail: "“We are still blocked on the payment gateway.”" },
    ],
  },

  followThread: {
    id: "thread",
    label: "Follow the thread",
    headline: "Follow the thread.",
    body: "Every observation is a point on a thread that runs from a meeting, through an entity, into memory, and back out as intelligence, evidence, and action.",
    stages: [
      { id: "meeting", label: "Meeting", detail: "Conversation happens" },
      { id: "observation", label: "Observation", detail: "Facts are extracted" },
      { id: "entity", label: "Entity", detail: "Mentions are resolved" },
      { id: "timeline", label: "Timeline", detail: "History is preserved" },
      { id: "change", label: "Change", detail: "Transitions are detected" },
      { id: "attention", label: "Attention", detail: "Signals are prioritised" },
      { id: "evidence", label: "Evidence", detail: "Sources are cited" },
      { id: "action", label: "Action", detail: "Follow-up is recommended" },
    ],
  },

  security: {
    id: "security",
    label: "Security",
    headline: "Built on trust, not promises.",
    body: "ThreadLine is a single-node system with tenant isolation enforced where data is read and written — not as a route-level convention.",
    characteristics: [
      "Authenticated access with server-side sessions",
      "Organisation-scoped data access and tenant isolation",
      "Role-aware permissions for owners, admins, and members",
      "Durable source state in SQLite",
      "Evidence-backed intelligence with cited sources",
      "Durable background processing for long-running work",
    ],
  },

  finalCta: {
    headline: "Start building organisational memory.",
    body: "See what ThreadLine can help your organisation remember, investigate, and act on.",
    cta: "Open ThreadLine",
    ctaHref: "/app",
  },

  footer: {
    brand: "ThreadLine",
    tagline: "Organisational memory from your meetings.",
  },
} as const;
