import type { AppRoute } from "@/routes";

import { NotFoundPage } from "./NotFoundPage";

export const route: AppRoute = {
  path: "*",
  element: <NotFoundPage />,
  requiresAuth: false,
};
