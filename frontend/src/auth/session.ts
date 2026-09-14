/* Session token storage.
 *
 * sessionStorage (tab-scoped): survives reloads in the same tab, never
 * shared across tabs, cleared when the tab closes. Tokens are never logged
 * and never placed in URLs.
 */

const TOKEN_KEY = "tl.session.token";
const ORG_KEY_PREFIX = "tl.org.";

function storage(): Storage | null {
  try {
    if (typeof sessionStorage !== "undefined") return sessionStorage;
  } catch {
    // Storage unavailable (privacy mode): fall back to memory below.
  }
  return null;
}

let memoryToken: string | null = null;

export function readToken(): string | null {
  const store = storage();
  if (store) {
    try {
      return store.getItem(TOKEN_KEY);
    } catch {
      return memoryToken;
    }
  }
  return memoryToken;
}

export function writeToken(token: string | null): void {
  const store = storage();
  if (token === null) {
    memoryToken = null;
    try {
      store?.removeItem(TOKEN_KEY);
    } catch {
      /* ignore */
    }
    return;
  }
  memoryToken = token;
  try {
    store?.setItem(TOKEN_KEY, token);
  } catch {
    /* ignore — memory copy above still works for this tab */
  }
}

export function readOrganisationId(userId: string): string | null {
  try {
    return storage()?.getItem(`${ORG_KEY_PREFIX}${userId}`) ?? null;
  } catch {
    return null;
  }
}

export function writeOrganisationId(userId: string, organisationId: string | null): void {
  try {
    const store = storage();
    if (!store) return;
    if (organisationId === null) store.removeItem(`${ORG_KEY_PREFIX}${userId}`);
    else store.setItem(`${ORG_KEY_PREFIX}${userId}`, organisationId);
  } catch {
    /* ignore */
  }
}
