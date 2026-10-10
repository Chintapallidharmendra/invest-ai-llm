import type { AppRoute } from "@/routes";

import { SetPasswordPage } from "./SetPasswordPage";

export const route: AppRoute = {
  path: "/set-password",
  element: <SetPasswordPage />,
  requiresAuth: false,
  layout: "bare",
};
