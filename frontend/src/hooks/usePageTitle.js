import { useEffect } from "react";
import { useLocation, matchPath } from "react-router-dom";

const ROUTES = [
  { path: "/login", title: "Sign in" },
  { path: "/", title: "Dashboard" },
  { path: "/vendors", title: "Vendors" },
  { path: "/vendors/new", title: "New vendor" },
  { path: "/vendors/:id/edit", title: "Edit vendor" },
  { path: "/vendors/:id", title: "Vendor detail" },
  { path: "/tasks", title: "Task allocation" },
  { path: "/tasks/:id", title: "Work order" },
  { path: "/validate/:woId", title: "Validation" },
  { path: "/review", title: "Review queue" },
  { path: "/users", title: "Users" },
  { path: "/survey", title: "Field survey" },
  { path: "/survey/admin", title: "Survey admin" },
  { path: "/tracking", title: "Tracking" },
  { path: "/reports", title: "Reports" },
  { path: "/detection", title: "Detection" },
  { path: "/model-bench", title: "Model testing" },
  { path: "/upload", title: "Field upload" },
  { path: "/capture", title: "Capture" },
];

export function usePageTitle() {
  const { pathname } = useLocation();

  useEffect(() => {
    const match = ROUTES.find((r) => matchPath({ path: r.path, end: true }, pathname));
    const page = match?.title || "Portal";
    document.title = `${page} · SmartRoad Operations`;
  }, [pathname]);
}
