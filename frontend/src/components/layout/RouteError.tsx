import { isRouteErrorResponse, useRouteError } from "react-router";

import { isProblemError } from "@/api/problem";
import { NotFoundPage } from "@/routes/not-found/NotFoundPage";

/** Router error boundary: 404s look like a missing page; anything else is generic. */
export function RouteError() {
  const error = useRouteError();
  const status = isRouteErrorResponse(error) ? error.status : isProblemError(error) ? error.status : 500;
  if (status === 404) return <NotFoundPage />;
  const correlationId = isProblemError(error) ? error.correlationId : undefined;
  return (
    <section role="alert" className="mx-auto max-w-md py-16 text-center">
      <h1 className="text-2xl font-semibold">Something went wrong</h1>
      <p className="mt-2 text-sm text-muted-foreground">Reload the page or try again later.</p>
      {correlationId ? (
        <p className="mt-4 text-xs text-muted-foreground">Reference: {correlationId}</p>
      ) : null}
    </section>
  );
}
