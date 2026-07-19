import { TrendingUp } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { SearchBar } from "@/components/SearchBar";

export function AppLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-10 border-b border-border bg-background/95 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-4 px-4 py-3">
          <Link to="/" className="flex shrink-0 items-center gap-2 font-semibold">
            <TrendingUp className="h-5 w-5 text-primary" />
            <span>YieldLens</span>
          </Link>
          <div className="flex-1">
            <SearchBar />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      <footer className="mx-auto max-w-6xl px-4 py-6 text-xs text-muted-foreground">
        数据仅供参考，不构成投资建议。股价与分红数据来源于公开数据源，可能存在延迟。
      </footer>
    </div>
  );
}
