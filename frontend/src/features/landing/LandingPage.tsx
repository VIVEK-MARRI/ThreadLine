/* LandingPage — ThreadLine public landing page (Stage 35 replacement).
 * A continuous product story: problem -> meeting-to-memory -> entity ->
 * organisation intelligence -> proactive signals -> evidence -> ask ->
 * follow-the-thread -> security -> call to action.
 * Uses landing-scoped premium styling over existing design tokens.
 * No auth context required; CTAs route to the real /app flow. */

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Menu, X } from "lucide-react";
import { Logo } from "../../components/layout/Logo";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { LANDING } from "./landingContent";
import { useReveal } from "./useLandingAnimations";
import { ThreadDiagram } from "./ThreadDiagram";
import { MemoryTransformation } from "./MemoryTransformation";
import { EntityMap } from "./EntityMap";
import { EvidenceChain } from "./EvidenceChain";
import { IntelligenceSignal } from "./IntelligenceSignal";
import { AskIllustration } from "./AskIllustration";
import { FollowThreadDiagram } from "./FollowThreadDiagram";

/* ------------------------------------------------------------------ */
/* Navigation                                                          */
/* ------------------------------------------------------------------ */

function LandingNav(): React.JSX.Element {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuBtnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    function onKeyDown(e: KeyboardEvent): void {
      if (e.key === "Escape") {
        setMenuOpen(false);
        menuBtnRef.current?.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [menuOpen]);

  return (
    <header className="tl-landing-nav" role="banner">
      <div className="tl-landing-nav-inner">
        <a href="/" className="tl-landing-brand" aria-label="ThreadLine home">
          <Logo />
        </a>

        <nav className="tl-landing-links" aria-label="Landing page">
          {LANDING.nav.links.map((link) => (
            <a key={link.href} href={link.href} className="tl-landing-link">
              {link.label}
            </a>
          ))}
        </nav>

        <Link to={LANDING.nav.ctaHref} className="tl-btn tl-btn-primary tl-landing-cta-btn">
          {LANDING.nav.cta}
        </Link>

        <button
          type="button"
          ref={menuBtnRef}
          className="tl-icon-btn tl-landing-menu-btn"
          aria-label={menuOpen ? LANDING.nav.mobileCloseLabel : LANDING.nav.mobileMenuLabel}
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((o) => !o)}
        >
          {menuOpen ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
        </button>
      </div>

      {menuOpen && (
        <div className="tl-landing-mobile-menu" role="navigation" aria-label="Mobile navigation">
          {LANDING.nav.links.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className="tl-landing-mobile-link"
              onClick={() => setMenuOpen(false)}
            >
              {link.label}
            </a>
          ))}
          <Link
            to={LANDING.nav.ctaHref}
            className="tl-btn tl-btn-primary tl-btn-full"
            onClick={() => setMenuOpen(false)}
          >
            {LANDING.nav.cta}
          </Link>
        </div>
      )}
    </header>
  );
}

/* ------------------------------------------------------------------ */
/* Section shell                                                       */
/* ------------------------------------------------------------------ */

function Section({
  id,
  className = "",
  children,
  narrow = false,
  labelledBy,
  dark = false,
}: {
  id?: string;
  className?: string;
  children: React.ReactNode;
  narrow?: boolean;
  labelledBy?: string;
  dark?: boolean;
}): React.JSX.Element {
  const { ref, revealed } = useReveal(0.08);
  return (
    <section
      id={id}
      ref={ref as React.RefObject<HTMLElement>}
      aria-labelledby={labelledBy}
      data-theme={dark ? "dark" : undefined}
      className={`tl-landing-section ${revealed ? "tl-revealed" : ""} ${narrow ? "tl-landing-narrow" : ""} ${className}`}
    >
      {children}
    </section>
  );
}

function SectionLabel({ children, tone = "default" }: { children: React.ReactNode; tone?: "default" | "inverse" }): React.JSX.Element {
  return <p className={`tl-landing-section-label${tone === "inverse" ? " tl-landing-section-label-inverse" : ""}`}>{children}</p>;
}

/* ------------------------------------------------------------------ */
/* Hero                                                                */
/* ------------------------------------------------------------------ */

function HeroSection(): React.JSX.Element {
  return (
    <section className="tl-landing-hero" aria-labelledby="hero-heading">
      <div className="tl-landing-hero-light" aria-hidden="true" />
      <div className="tl-landing-hero-inner">
        <div className="tl-landing-hero-copy">
          <h1 id="hero-heading" className="tl-landing-hero-headline">
            {LANDING.hero.headline}
          </h1>
          <p className="tl-landing-hero-body">{LANDING.hero.body}</p>
          <div className="tl-landing-hero-actions">
            <Link to={LANDING.hero.primaryCtaHref} className="tl-btn tl-btn-primary tl-landing-hero-cta">
              {LANDING.hero.primaryCta}
            </Link>
            <a href={LANDING.hero.secondaryCtaHref} className="tl-btn tl-btn-secondary">
              {LANDING.hero.secondaryCta}
            </a>
          </div>
        </div>
        <div className="tl-landing-hero-visual">
          <div className="tl-hero-glass tl-glass" aria-hidden="true" />
          <div className="tl-hero-thread">
            <ThreadDiagram animate />
          </div>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* The Problem: fragmented -> connected                                */
/* ------------------------------------------------------------------ */

function ProblemSection(): React.JSX.Element {
  const { ref, revealed } = useReveal(0.2);
  return (
    <section
      id={LANDING.problem.id}
      ref={ref as React.RefObject<HTMLElement>}
      aria-labelledby="problem-heading"
      className={`tl-landing-section tl-landing-narrow tl-landing-problem ${revealed ? "tl-revealed" : ""}`}
    >
      <SectionLabel>{LANDING.problem.label}</SectionLabel>
      <h2 id="problem-heading" className="tl-landing-h2">
        {LANDING.problem.headline}
      </h2>
      <p className="tl-landing-body">{LANDING.problem.body}</p>
      <div
        className="tl-fragments"
        role="group"
        aria-label="Scattered fragments of organisational context"
      >
        {LANDING.problem.fragments.map((fragment, i) => (
          <span
            key={fragment}
            className="tl-fragment-chip"
            style={{ animationDelay: `${0.1 + i * 0.12}s` }}
          >
            {fragment}
          </span>
        ))}
        <div className="tl-fragments-thread" aria-hidden="true" />
      </div>
      <p className="tl-landing-body tl-fragments-resolution">{LANDING.problem.resolution}</p>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Meeting to Memory                                                   */
/* ------------------------------------------------------------------ */

function MeetingToMemorySection(): React.JSX.Element {
  return (
    <Section id={LANDING.meetingToMemory.id} labelledBy="mtm-heading">
      <SectionLabel>{LANDING.meetingToMemory.label}</SectionLabel>
      <h2 id="mtm-heading" className="tl-landing-h2">
        {LANDING.meetingToMemory.headline}
      </h2>
      <p className="tl-landing-body tl-landing-narrow">{LANDING.meetingToMemory.body}</p>
      <MemoryTransformation />
    </Section>
  );
}

/* ------------------------------------------------------------------ */
/* Entity Intelligence                                                 */
/* ------------------------------------------------------------------ */

function EntityIntelligenceSection(): React.JSX.Element {
  return (
    <Section id={LANDING.entityIntelligence.id} labelledBy="entity-heading">
      <SectionLabel>{LANDING.entityIntelligence.label}</SectionLabel>
      <h2 id="entity-heading" className="tl-landing-h2">
        {LANDING.entityIntelligence.headline}
      </h2>
      <p className="tl-landing-body tl-landing-narrow">{LANDING.entityIntelligence.body}</p>
      <EntityMap />
    </Section>
  );
}

/* ------------------------------------------------------------------ */
/* Organisation Intelligence (dark contrast)                           */
/* ------------------------------------------------------------------ */

function OrgIntelligenceSection(): React.JSX.Element {
  return (
    <div className="tl-landing-dark-band">
      <Section id={LANDING.orgIntelligence.id} labelledBy="org-heading" dark>
        <SectionLabel tone="inverse">{LANDING.orgIntelligence.label}</SectionLabel>
        <h2 id="org-heading" className="tl-landing-h2 tl-landing-h2-inverse">
          {LANDING.orgIntelligence.headline}
        </h2>
        <p className="tl-landing-body tl-landing-body-inverse tl-landing-narrow">
          {LANDING.orgIntelligence.body}
        </p>
        <ol className="tl-org-flow">
          {LANDING.orgIntelligence.flow.map((step, i) => (
            <li
              key={step.label}
              className="tl-org-flow-step"
              style={{ animationDelay: `${0.1 + i * 0.12}s` }}
            >
              <span className="tl-org-flow-index" aria-hidden="true">
                {String(i + 1).padStart(2, "0")}
              </span>
              <div>
                <p className="tl-org-flow-label">{step.label}</p>
                <p className="tl-org-flow-detail">{step.detail}</p>
              </div>
            </li>
          ))}
        </ol>
      </Section>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Proactive Intelligence                                              */
/* ------------------------------------------------------------------ */

function ProactiveSection(): React.JSX.Element {
  return (
    <Section id={LANDING.proactive.id} labelledBy="proactive-heading">
      <SectionLabel>{LANDING.proactive.label}</SectionLabel>
      <h2 id="proactive-heading" className="tl-landing-h2">
        {LANDING.proactive.headline}
      </h2>
      <p className="tl-landing-body tl-landing-narrow">{LANDING.proactive.body}</p>
      <IntelligenceSignal />
      <div className="tl-landing-flow" role="list" aria-label="Proactive intelligence flow">
        {LANDING.proactive.flow.map((step, i) => (
          <div key={i} className="tl-landing-flow-step" role="listitem">
            {i > 0 && <span className="tl-landing-flow-arrow" aria-hidden="true" />}
            <span className="tl-landing-flow-label">{step}</span>
          </div>
        ))}
      </div>
      <p className="tl-landing-caveat">{LANDING.proactive.caveat}</p>
    </Section>
  );
}

/* ------------------------------------------------------------------ */
/* Evidence                                                            */
/* ------------------------------------------------------------------ */

function EvidenceSection(): React.JSX.Element {
  return (
    <Section id={LANDING.evidence.id} labelledBy="evidence-heading">
      <SectionLabel>{LANDING.evidence.label}</SectionLabel>
      <h2 id="evidence-heading" className="tl-landing-h2">
        {LANDING.evidence.headline}
      </h2>
      <p className="tl-landing-body tl-landing-narrow">{LANDING.evidence.body}</p>
      <EvidenceChain />
    </Section>
  );
}

/* ------------------------------------------------------------------ */
/* Ask ThreadLine                                                      */
/* ------------------------------------------------------------------ */

function AskSection(): React.JSX.Element {
  return (
    <Section id={LANDING.ask.id} labelledBy="ask-heading">
      <SectionLabel>{LANDING.ask.label}</SectionLabel>
      <h2 id="ask-heading" className="tl-landing-h2">
        {LANDING.ask.headline}
      </h2>
      <p className="tl-landing-body tl-landing-narrow">{LANDING.ask.body}</p>
      <AskIllustration />
    </Section>
  );
}

/* ------------------------------------------------------------------ */
/* Follow the Thread (dark centerpiece)                                */
/* ------------------------------------------------------------------ */

function FollowThreadSection(): React.JSX.Element {
  return (
    <div className="tl-landing-dark-band tl-landing-dark-band-thread">
      <Section
        id={LANDING.followThread.id}
        labelledBy="thread-heading"
        dark
        className="tl-landing-follow-section"
      >
        <SectionLabel tone="inverse">{LANDING.followThread.label}</SectionLabel>
        <h2 id="thread-heading" className="tl-landing-h2 tl-landing-h2-inverse">
          {LANDING.followThread.headline}
        </h2>
        <p className="tl-landing-body tl-landing-body-inverse tl-landing-narrow">
          {LANDING.followThread.body}
        </p>
        <FollowThreadDiagram />
      </Section>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Security                                                            */
/* ------------------------------------------------------------------ */

function SecuritySection(): React.JSX.Element {
  return (
    <Section id={LANDING.security.id} labelledBy="security-heading" narrow>
      <SectionLabel>{LANDING.security.label}</SectionLabel>
      <h2 id="security-heading" className="tl-landing-h2">
        {LANDING.security.headline}
      </h2>
      <p className="tl-landing-body">{LANDING.security.body}</p>
      <ul className="tl-landing-security-list">
        {LANDING.security.characteristics.map((c, i) => (
          <li key={i}>
            <svg
              className="tl-landing-check-icon"
              viewBox="0 0 20 20"
              width="20"
              height="20"
              aria-hidden="true"
            >
              <path
                d="M5 10l3 3 7-7"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            <span>{c}</span>
          </li>
        ))}
      </ul>
    </Section>
  );
}

/* ------------------------------------------------------------------ */
/* Final CTA + footer                                                  */
/* ------------------------------------------------------------------ */

function FinalCtaSection(): React.JSX.Element {
  return (
    <Section labelledBy="cta-heading" narrow className="tl-landing-final-cta">
      <h2 id="cta-heading" className="tl-landing-h2">
        {LANDING.finalCta.headline}
      </h2>
      <p className="tl-landing-body">{LANDING.finalCta.body}</p>
      <Link to={LANDING.finalCta.ctaHref} className="tl-btn tl-btn-primary tl-landing-hero-cta">
        {LANDING.finalCta.cta}
      </Link>
    </Section>
  );
}

function LandingFooter(): React.JSX.Element {
  return (
    <footer className="tl-landing-footer" role="contentinfo">
      <div className="tl-landing-footer-inner">
        <Logo compact />
        <p className="tl-landing-footer-tagline">{LANDING.footer.tagline}</p>
      </div>
    </footer>
  );
}

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export function LandingPage(): React.JSX.Element {
  useDocumentTitle(LANDING.meta.title);

  useEffect(() => {
    const ogTags: HTMLMetaElement[] = [];
    function setOg(property: string, content: string): void {
      let el = document.querySelector<HTMLMetaElement>(`meta[property="${property}"]`);
      if (!el) {
        el = document.createElement("meta");
        el.setAttribute("property", property);
        document.head.appendChild(el);
        ogTags.push(el);
      }
      el.setAttribute("content", content);
    }
    setOg("og:title", LANDING.meta.title);
    setOg("og:description", LANDING.meta.description);
    setOg("og:type", LANDING.meta.ogType);
    return () => {
      for (const el of ogTags) el.remove();
    };
  }, []);

  return (
    <div className="tl-landing">
      <a href="#tl-landing-main" className="tl-skip-link">
        {LANDING.nav.skipLink}
      </a>
      <LandingNav />
      <main id="tl-landing-main">
        <HeroSection />
        <ProblemSection />
        <MeetingToMemorySection />
        <EntityIntelligenceSection />
        <OrgIntelligenceSection />
        <ProactiveSection />
        <EvidenceSection />
        <AskSection />
        <FollowThreadSection />
        <SecuritySection />
        <FinalCtaSection />
      </main>
      <LandingFooter />
    </div>
  );
}
