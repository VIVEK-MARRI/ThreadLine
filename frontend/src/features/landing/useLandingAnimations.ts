/* useLandingAnimations — IntersectionObserver hooks for scroll-triggered reveals.
 * Lightweight, no dependencies. Respects prefers-reduced-motion. */

import { useEffect, useRef, useState } from "react";

/** True when the visitor prefers reduced motion. Used to skip staged sequences. */
export function usePrefersReducedMotion(): boolean {  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(query.matches);
    function onChange(event: MediaQueryListEvent): void {
      setReduced(event.matches);
    }
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  return reduced;
}

/** True when a CSS media query matches. Defaults to false when
 * matchMedia is unavailable (e.g. jsdom unit tests render desktop). */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const list = window.matchMedia(query);
    setMatches(list.matches);
    function onChange(event: MediaQueryListEvent): void {
      setMatches(event.matches);
    }
    list.addEventListener("change", onChange);
    return () => list.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}

/** Returns true once the element has entered the viewport. */
export function useReveal(threshold = 0.15): {
  ref: React.RefObject<HTMLElement | null>;
  revealed: boolean;
} {
  const ref = useRef<HTMLElement | null>(null);
  const [revealed, setRevealed] = useState(false);

  useEffect(() => {
    const prefersReduced =
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (prefersReduced) {
      setRevealed(true);
      return;
    }

    const el = ref.current;
    if (!el) return;

    if (typeof IntersectionObserver === "undefined") {
      setRevealed(true);
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setRevealed(true);
          observer.disconnect();
        }
      },
      { threshold },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [threshold]);

  return { ref, revealed };
}

/** Returns an array of booleans tracking which stages have been reached. */
export function useStageReveal(count: number, threshold = 0.3): {
  refs: React.RefObject<(HTMLElement | null)[]>;
  active: boolean[];
} {
  const refs = useRef<(HTMLElement | null)[]>(new Array(count).fill(null));
  const [active, setActive] = useState<boolean[]>(new Array(count).fill(false));

  useEffect(() => {
    const prefersReduced =
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (prefersReduced) {
      setActive(new Array(count).fill(true));
      return;
    }

    if (typeof IntersectionObserver === "undefined") {
      setActive(new Array(count).fill(true));
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        setActive((prev) => {
          const next = [...prev];
          for (const entry of entries) {
            const idx = refs.current.indexOf(entry.target as HTMLElement);
            if (idx >= 0 && entry.isIntersecting) next[idx] = true;
          }
          return next;
        });
      },
      { threshold },
    );

    for (const el of refs.current) {
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, [count, threshold]);

  return { refs, active };
}
