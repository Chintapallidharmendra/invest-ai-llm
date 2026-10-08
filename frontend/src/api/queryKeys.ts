/**
 * The central query-key factory (ADR-008). Mutations invalidate by these keys.
 *
 * Feature stories don't edit this file: they create their keys with `keyScope` in
 * `src/features/<feature>/queryKeys.ts`, and invalidate a whole feature with `.all`.
 */

export interface KeyScope<S extends string> {
  /** Every key in the scope; pass to `invalidateQueries({ queryKey })`. */
  readonly all: readonly [S];
  key<const P extends readonly unknown[]>(...parts: P): readonly [S, ...P];
}

export function keyScope<const S extends string>(scope: S): KeyScope<S> {
  return {
    all: [scope] as const,
    key: (...parts) => [scope, ...parts] as const,
  };
}

const auth = keyScope("auth");

export const queryKeys = {
  auth: {
    all: auth.all,
    /** The current user and CSRF state (`GET /auth/me`, wired in Story 2.5). */
    me: () => auth.key("me"),
  },
} as const;
