import { Menu } from "lucide-react";
import { Link } from "react-router";

import { useAuth } from "@/auth/RequireAuth";
import { Button } from "@/components/ui/button";

export interface HeaderProps {
  navOpen: boolean;
  onToggleNav: () => void;
}

export function Header({ navOpen, onToggleNav }: HeaderProps) {
  const auth = useAuth();
  return (
    <header className="sticky top-0 z-40 flex h-14 items-center gap-3 border-b bg-background px-4">
      <Button
        variant="ghost"
        size="icon"
        className="md:hidden"
        aria-label={navOpen ? "Close navigation" : "Open navigation"}
        aria-expanded={navOpen}
        aria-controls="app-navigation"
        onClick={onToggleNav}
      >
        <Menu aria-hidden="true" />
      </Button>
      <Link to="/" className="rounded-sm text-base font-semibold tracking-tight">
        invest-ai-llm
      </Link>
      <div className="ml-auto flex items-center gap-2 text-sm text-muted-foreground">
        {auth.status === "authenticated" ? <span>{auth.user.username}</span> : null}
      </div>
    </header>
  );
}
