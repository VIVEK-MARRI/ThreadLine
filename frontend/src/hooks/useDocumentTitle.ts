import { useEffect } from "react";

/** Keep the document title in sync with the current screen. */
export function useDocumentTitle(title: string): void {
  useEffect(() => {
    const previous = document.title;
    document.title = `${title} · ThreadLine`;
    return () => {
      document.title = previous;
    };
  }, [title]);
}
