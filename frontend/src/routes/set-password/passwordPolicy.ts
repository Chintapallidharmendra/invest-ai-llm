/** The password policy as users see it, and plain messages for the server's reason codes. */

export const POLICY_HINT = "At least 12 characters, and not a common password.";

const REASONS: Record<string, string> = {
  too_short: "Use at least 12 characters.",
  too_long: "Use at most 1024 characters.",
  breached: "This password is too common. Choose a different one.",
  contains_username: "Don't include your username in the password.",
};

/** Messages for the `reasons` of a `422 password_rejected` problem. */
export function reasonMessages(reasons: unknown): string[] {
  if (!Array.isArray(reasons) || reasons.length === 0) return ["Choose a stronger password."];
  return reasons.map((r) => (typeof r === "string" && REASONS[r]) || "Choose a stronger password.");
}
