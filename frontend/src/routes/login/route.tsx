import type { AppRoute } from "@/routes";

import { LoginPage } from "./LoginPage";

export const route: AppRoute = {
  path: "/login",
  element: <LoginPage />,
  requiresAuth: false,
  layout: "bare",
};
