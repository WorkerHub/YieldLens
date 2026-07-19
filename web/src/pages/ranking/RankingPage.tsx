import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { StockTable } from "@/components/StockTable";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { Stock, StockListParams, StockListResponse } from "@/types";

const PAGE_SIZE = 50;

export function RankingPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [stocks, setStocks] = useState<Stock[]>([]);
  const [total, setTotal] = useState(0);
  const [industries, setIndustries] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  const page = Number.parseInt(searchParams.get("page") ?? "1", 10);
  const sort = (searchParams.get("sort") ?? "ttm_yield") as StockListParams["sort"];
  const order = (searchParams.get("order") ?? "desc") as StockListParams["order"];
  const industry = searchParams.get("industry") ?? "";
  const minYield = searchParams.get("min_yield") ?? "";
  const excludeSt = searchParams.get("exclude_st") === "true";

  useEffect(() => {
    api.get<{ data: string[] }>("/stocks/industries").then((res) => setIndustries(res.data));
  }, []);

  useEffect(() => {
    setLoading(true);
    const params = new URLSearchParams({
      page: String(page),
      pageSize: String(PAGE_SIZE),
      sort,
      order,
    });
    if (industry) params.set("industry", industry);
    if (minYield) params.set("min_yield", minYield);
    if (excludeSt) params.set("exclude_st", "true");

    api
      .get<StockListResponse>(`/stocks?${params.toString()}`)
      .then((res) => {
        setStocks(res.data);
        setTotal(res.total);
      })
      .finally(() => setLoading(false));
  }, [page, sort, order, industry, minYield, excludeSt]);

  function updateParam(key: string, value: string | null) {
    const next = new URLSearchParams(searchParams);
    if (value === null || value === "") {
      next.delete(key);
    } else {
      next.set(key, value);
    }
    next.delete("page");
    setSearchParams(next);
  }

  function handleSortChange(newSort: StockListParams["sort"]) {
    const next = new URLSearchParams(searchParams);
    if (sort === newSort) {
      next.set("order", order === "desc" ? "asc" : "desc");
    } else {
      next.set("sort", newSort);
      next.set("order", "desc");
    }
    next.delete("page");
    setSearchParams(next);
  }

  function goToPage(newPage: number) {
    const next = new URLSearchParams(searchParams);
    next.set("page", String(newPage));
    setSearchParams(next);
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold">股息率排行</h1>
        <p className="text-sm text-muted-foreground">按股息率排序筛选全市场A股，点击行查看详情</p>
      </div>

      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-border bg-card p-4">
        <div className="flex flex-col gap-1">
          <label htmlFor="industry-select" className="text-xs text-muted-foreground">
            行业
          </label>
          <select
            id="industry-select"
            className="h-9 rounded-md border border-input bg-background px-3 text-sm"
            value={industry}
            onChange={(e) => updateParam("industry", e.target.value || null)}
          >
            <option value="">全部行业</option>
            {industries.map((i) => (
              <option key={i} value={i}>
                {i}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="min-yield-input" className="text-xs text-muted-foreground">
            最低股息率(%)
          </label>
          <Input
            id="min-yield-input"
            type="number"
            step="0.1"
            className="w-28"
            value={minYield}
            onChange={(e) => updateParam("min_yield", e.target.value || null)}
          />
        </div>
        <label className="flex items-center gap-2 pb-2 text-sm">
          <input
            type="checkbox"
            checked={excludeSt}
            onChange={(e) => updateParam("exclude_st", e.target.checked ? "true" : null)}
          />
          排除ST/*ST
        </label>
      </div>

      {loading ? (
        <div className="py-12 text-center text-muted-foreground">加载中...</div>
      ) : (
        <>
          <StockTable stocks={stocks} sort={sort} order={order} onSortChange={handleSortChange} />
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>共 {total} 只股票</span>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() => goToPage(page - 1)}
              >
                上一页
              </Button>
              <span>
                {page} / {totalPages}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPages}
                onClick={() => goToPage(page + 1)}
              >
                下一页
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
