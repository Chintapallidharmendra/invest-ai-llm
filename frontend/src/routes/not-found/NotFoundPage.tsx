import { Link } from "react-router";

import { Button } from "@/components/ui/button";

export function NotFoundPage() {
  return (
    <section aria-labelledby="not-found-title" className="mx-auto max-w-md py-16 text-center">
      <p className="text-sm font-medium text-muted-foreground">404</p>
      <h1 id="not-found-title" className="mt-2 text-2xl font-semibold">
        Page not found
      </h1>
      <p className="mt-2 text-sm text-muted-foreground">
        This page doesn't exist, or you don't have access to it.
      </p>
      <Button asChild className="mt-6">
        <Link to="/">Go to home</Link>
      </Button>
    </section>
  );
}
