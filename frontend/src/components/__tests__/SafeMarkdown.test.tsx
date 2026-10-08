import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { parseCitation, SafeMarkdown } from "@/components/SafeMarkdown";

function renderMd(markdown: string) {
  const onCite = vi.fn<(documentId: string, locator: string) => void>();
  const { container } = render(<SafeMarkdown onCite={onCite}>{markdown}</SafeMarkdown>);
  return { container, onCite };
}

describe("SafeMarkdown", () => {
  it("renders a cite: link as a button that calls onCite", () => {
    const { container, onCite } = renderMd("Revenue rose [p.12](cite:abc#p.12).");
    const button = screen.getByRole("button", { name: "p.12" });
    fireEvent.click(button);
    expect(onCite).toHaveBeenCalledWith("abc", "p.12");
    expect(container.querySelector("a")).toBeNull();
  });

  it.each([
    ["<cite:doc-1#slide 4>", "doc-1", "slide 4"],
    ["cite:doc_2#Sheet1!B2:F20", "doc_2", "Sheet1!B2:F20"],
    ["cite:0192f0c4-7a1e-7bbb-9c3d-1f2e3d4c5b6a#p.3", "0192f0c4-7a1e-7bbb-9c3d-1f2e3d4c5b6a", "p.3"],
  ])("decodes locators: %s", (dest, documentId, locator) => {
    const { onCite } = renderMd(`[source](${dest})`);
    fireEvent.click(screen.getByRole("button", { name: "source" }));
    expect(onCite).toHaveBeenCalledWith(documentId, locator);
  });

  it.each([
    ["external link", "[x](https://evil.example)", "x"],
    ["javascript: href", "[click](javascript:alert(1))", "click"],
    ["relative link", "[admin](/admin)", "admin"],
    ["mailto", "[mail](mailto:a@b.example)", "mail"],
    ["autolink", "<https://evil.example/path>", "https://evil.example/path"],
    ["cite without locator", "[bad](cite:abc)", "bad"],
    ["cite with empty locator", "[bad](cite:abc#)", "bad"],
    ["cite with bad document id", "[bad](cite:..%2Fetc#p.1)", "bad"],
    ["cite with markup in locator", "[bad](cite:abc#%3Cscript%3E)", "bad"],
    ["cite with malformed escape", "[bad](cite:abc#%E0%A4%A)", "bad"],
  ])("renders %s as plain text", (_label, markdown, text) => {
    const { container } = renderMd(markdown);
    expect(container.querySelector("a")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    expect(container).toHaveTextContent(text);
  });

  it("renders no images", () => {
    const { container } = renderMd("before ![i](http://x/y.png) after");
    expect(container.querySelector("img")).toBeNull();
    expect(container).toHaveTextContent("before after");
  });

  it("renders no raw HTML", () => {
    const { container } = renderMd(
      'a <img src=x onerror="alert(1)"> b <script>alert(1)</script> <a href="https://evil.example">c</a>\n\n<div onclick="x()">block</div>',
    );
    expect(container.querySelector("img, script, div[onclick], a")).toBeNull();
    expect(container.innerHTML).not.toContain("onerror");
    expect(container.innerHTML).not.toContain("evil.example");
  });

  it("keeps ordinary markdown formatting", () => {
    const { container } = renderMd("# Title\n\n**bold** and *em*\n\n- one\n- two\n\n`code`");
    expect(screen.getByRole("heading", { name: "Title" })).toBeInTheDocument();
    expect(container.querySelector("strong")).toHaveTextContent("bold");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(container.querySelector("code")).toHaveTextContent("code");
  });
});

describe("parseCitation", () => {
  it("parses well-formed citations and rejects the rest", () => {
    expect(parseCitation("cite:abc#p.12")).toEqual({ documentId: "abc", locator: "p.12" });
    expect(parseCitation("cite:abc#slide%204")).toEqual({ documentId: "abc", locator: "slide 4" });
    expect(parseCitation("https://x#p.1")).toBeUndefined();
    expect(parseCitation("cite:#p.1")).toBeUndefined();
    expect(parseCitation("cite:abc#%0Ap.1")).toBeUndefined();
    expect(parseCitation(undefined)).toBeUndefined();
  });
});
