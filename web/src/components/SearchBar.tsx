import { Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { formatYield } from "@/lib/utils";
import type { Stock } from "@/types";

export function SearchBar() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Stock[]>([]);
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (query.trim().length === 0) {
      setResults([]);
      return;
    }
    const handle = setTimeout(async () => {
      try {
        const res = await api.get<{ data: Stock[] }>(`/stocks/search?q=${encodeURIComponent(query.trim())}`);
        setResults(res.data);
        setOpen(true);
      } catch {
        setResults([]);
      }
    }, 300);
    return () => clearTimeout(handle);
  }, [query]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  function goToStock(code: string) {
    setOpen(false);
    setQuery("");
    navigate(`/stocks/${code}`);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && results.length > 0) {
      goToStock(results[0].code);
    }
  }

  return (
    <div ref={containerRef} className="relative max-w-sm">
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
        <Input
          placeholder="搜索股票代码或名称"
          className="pl-8"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => results.length > 0 && setOpen(true)}
          onKeyDown={handleKeyDown}
        />
      </div>
      {open && results.length > 0 && (
        <div className="absolute z-20 mt-1 w-full rounded-md border border-border bg-card shadow-md">
          {results.map((s) => (
            <button
              key={s.code}
              type="button"
              className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-muted/50"
              onClick={() => goToStock(s.code)}
            >
              <span>
                <span className="font-medium">{s.name}</span>
                <span className="ml-2 text-muted-foreground">{s.code}</span>
              </span>
              <span className="text-muted-foreground">{formatYield(s.ttm_yield)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
