/**
 * RFC 9457 problem details (ADR-003): the one error type the UI handles.
 *
 * The backend sends `application/problem+json` with `type`, `title`, `status`,
 * `detail`, `instance`, `code` and `correlation_id`; anything else is an extension
 * member (e.g. `errors` on `validation_error`). A 404 also means "not accessible",
 * so the UI shows "not found", never "forbidden".
 */

export const PROBLEM_MEDIA_TYPE = "application/problem+json";
export const CORRELATION_HEADER = "X-Correlation-ID";

const STANDARD_MEMBERS = new Set([
  "type",
  "title",
  "status",
  "detail",
  "instance",
  "code",
  "correlation_id",
]);

export interface ProblemInit {
  status: number;
  code: string;
  title: string;
  type?: string | undefined;
  detail?: string | undefined;
  instance?: string | undefined;
  correlationId?: string | undefined;
  extensions?: Record<string, unknown> | undefined;
}

export class ProblemError extends Error {
  override readonly name = "ProblemError";
  readonly status: number;
  readonly code: string;
  readonly title: string;
  readonly type: string;
  readonly detail: string | undefined;
  readonly instance: string | undefined;
  readonly correlationId: string | undefined;
  readonly extensions: Readonly<Record<string, unknown>>;

  constructor(init: ProblemInit) {
    super(init.title);
    this.status = init.status;
    this.code = init.code;
    this.title = init.title;
    this.type = init.type ?? "about:blank";
    this.detail = init.detail;
    this.instance = init.instance;
    this.correlationId = init.correlationId;
    this.extensions = init.extensions ?? {};
  }

  get isClientError(): boolean {
    return this.status >= 400 && this.status < 500;
  }
}

export function isProblemError(error: unknown): error is ProblemError {
  return error instanceof ProblemError;
}

function str(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function fallbackTitle(status: number): string {
  if (status === 404) return "Not found";
  if (status >= 500) return "The service is unavailable";
  return "Request failed";
}

/** Build a ProblemError from a parsed problem body (unknown shape tolerated). */
export function problemFromBody(
  body: unknown,
  status: number,
  correlationId?: string,
): ProblemError {
  const obj: Record<string, unknown> =
    typeof body === "object" && body !== null && !Array.isArray(body)
      ? (body as Record<string, unknown>)
      : {};
  const extensions: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(obj)) {
    if (!STANDARD_MEMBERS.has(key)) extensions[key] = value;
  }
  return new ProblemError({
    // The HTTP status wins over the body member.
    status,
    code: str(obj.code) ?? `http_${String(status)}`,
    title: str(obj.title) ?? fallbackTitle(status),
    type: str(obj.type),
    detail: str(obj.detail),
    instance: str(obj.instance),
    correlationId: str(obj.correlation_id) ?? correlationId,
    extensions,
  });
}

/** Turn any non-2xx response into a ProblemError; non-problem bodies get a generic one. */
export async function problemFromResponse(response: Response): Promise<ProblemError> {
  const correlationId = response.headers.get(CORRELATION_HEADER) ?? undefined;
  const contentType = response.headers.get("Content-Type") ?? "";
  let body: unknown = undefined;
  if (contentType.includes(PROBLEM_MEDIA_TYPE) || contentType.includes("application/json")) {
    try {
      body = await response.json();
    } catch {
      body = undefined;
    }
  }
  return problemFromBody(body, response.status, correlationId);
}
