import type { AppRoute } from "@/routes";

import { HomePage } from "./HomePage";

export const route: AppRoute = {
  path: "/",
  element: <HomePage />,
  requiresAuth: true,
  nav: { label: "Home", order: 0 },
};
