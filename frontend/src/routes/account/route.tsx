import type { AppRoute } from "@/routes";

import { ChangePasswordPage } from "./ChangePasswordPage";

export const route: AppRoute = {
  path: "/account/password",
  element: <ChangePasswordPage />,
  requiresAuth: true,
};
