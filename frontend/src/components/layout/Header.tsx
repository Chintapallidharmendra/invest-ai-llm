import { ChevronDown, KeyRound, LogOut, Menu } from "lucide-react";
import { Link } from "react-router";

import { useSignOut } from "@/auth/AuthProvider";
import { useAuth } from "@/auth/RequireAuth";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export interface HeaderProps {
  navOpen: boolean;
  onToggleNav: () => void;
}

function UserMenu({ username }: { username: string }) {
  const signOut = useSignOut();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" aria-label={`Account menu for ${username}`}>
          {username}
          <ChevronDown aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem asChild>
          <Link to="/account/password">
            <KeyRound aria-hidden="true" />
            Change password
          </Link>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={() => {
            void signOut();
          }}
        >
          <LogOut aria-hidden="true" />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
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
        {auth.status === "authenticated" ? <UserMenu username={auth.user.username} /> : null}
      </div>
    </header>
  );
}
