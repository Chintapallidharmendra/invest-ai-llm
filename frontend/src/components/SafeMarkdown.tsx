/**
 * The only markdown renderer for model output (ADR-008, ADR-025).
 *
 * - Raw HTML is dropped, never rendered.
 * - Images are dropped.
 * - The only links are internal citations `cite:<document_id>#<locator>` (e.g. `p.12`,
 *   `slide 4`, `Sheet1!B2:F20`), rendered as buttons that call `onCite`. Every other
 *   link, including a malformed `cite:`, is shown as its plain text.
 *
 * Three layers enforce this: `urlTransform` blanks non-`cite:` URLs, `rehype-sanitize`
 * strips anything outside the schema, and the `a` component re-validates the href.
 */

import type { ComponentProps } from "react";
import Markdown, { type Components } from "react-markdown";
import rehypeSanitize, { defaultSchema, type Options as SanitizeSchema } from "rehype-sanitize";

import { cn } from "@/lib/utils";

export const CITE_PROTOCOL = "cite:";

const DOCUMENT_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
// Printable text only: no control characters or markup-ish characters.
const LOCATOR = /^[^\p{Cc}<>"`\\]{1,200}$/u;

export interface Citation {
  documentId: string;
  locator: string;
}

/** Parse `cite:<document_id>#<locator>`; undefined when malformed. */
export function parseCitation(href: string | null | undefined): Citation | undefined {
  if (!href?.startsWith(CITE_PROTOCOL)) return undefined;
  const rest = href.slice(CITE_PROTOCOL.length);
  const hash = rest.indexOf("#");
  if (hash === -1) return undefined;
  let documentId: string;
  let locator: string;
  try {
    documentId = decodeURIComponent(rest.slice(0, hash));
    locator = decodeURIComponent(rest.slice(hash + 1));
  } catch {
    return undefined;
  }
  if (!DOCUMENT_ID.test(documentId) || !LOCATOR.test(locator)) return undefined;
  return { documentId, locator };
}

const sanitizeSchema: SanitizeSchema = {
  ...defaultSchema,
  tagNames: (defaultSchema.tagNames ?? []).filter(
    (tag) => !["img", "picture", "source", "video", "audio", "iframe", "input"].includes(tag),
  ),
  protocols: { href: ["cite"] },
  attributes: {
    ...defaultSchema.attributes,
    a: ["href"],
  },
};

function urlTransform(url: string): string {
  return url.startsWith(CITE_PROTOCOL) ? url : "";
}

export interface SafeMarkdownProps {
  children: string;
  onCite: (documentId: string, locator: string) => void;
  className?: string;
}

export function SafeMarkdown({ children, onCite, className }: SafeMarkdownProps) {
  const components: Components = {
    a: ({ href, children: text }: ComponentProps<"a">) => {
      const citation = parseCitation(href);
      if (!citation) return <span>{text}</span>;
      return (
        <button
          type="button"
          className="inline rounded-sm px-0.5 font-medium text-primary underline underline-offset-2 hover:no-underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          data-document-id={citation.documentId}
          data-locator={citation.locator}
          title={`Open source: ${citation.locator}`}
          onClick={() => {
            onCite(citation.documentId, citation.locator);
          }}
        >
          {text}
        </button>
      );
    },
    img: () => null,
  };

  return (
    <div className={cn("space-y-3 text-sm leading-relaxed [&_ol]:list-decimal [&_ol]:pl-5 [&_ul]:list-disc [&_ul]:pl-5", className)}>
      <Markdown
        skipHtml
        urlTransform={urlTransform}
        rehypePlugins={[[rehypeSanitize, sanitizeSchema]]}
        components={components}
      >
        {children}
      </Markdown>
    </div>
  );
}
