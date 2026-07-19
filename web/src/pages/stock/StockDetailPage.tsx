import { useEffect, useState } from "react";
import { useParams } from "react-router";
import { DividendTable } from "@/components/DividendTable";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { YieldChart } from "@/components/YieldChart";
import { api } from "@/lib/api";
import { formatMarketCap, formatPrice, formatUpdatedAt, formatYield } from "@/lib/utils";
import type { Dividend, StockDetail } from "@/types";

export function StockDetailPage() {
  const { code = "" } = useParams<{ code: string }>();
  const [stock, setStock] = useState<StockDetail | null>(null);
  const [dividends, setDividends] = useState<Dividend[]>([]);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    setLoading(true);
    setNotFound(false);
    Promise.all([
      api.get<{ data: StockDetail }>(`/stocks/${code}`),
      api.get<{ data: Dividend[] }>(`/stocks/${code}/dividends`),
    ])
      .then(([stockRes, dividendRes]) => {
        setStock(stockRes.data);
        setDividends(dividendRes.data);
      })
      .catch(() => setNotFound(true))
      .finally(() => setLoading(false));
  }, [code]);

  if (loading) {
    return <div className="py-12 text-center text-muted-foreground">加载中...</div>;
  }

  if (notFound || !stock) {
    return <div className="py-12 text-center text-muted-foreground">未找到该股票</div>;
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <div className="flex items-center gap-2">
          <h1 className="text-xl font-semibold">{stock.name}</h1>
          <span className="text-sm text-muted-foreground">{stock.code}</span>
          {stock.is_st === 1 && <Badge variant="warning">ST</Badge>}
          <Badge variant="muted">{stock.market === "A" ? "A股" : "港股通"}</Badge>
        </div>
        <p className="text-sm text-muted-foreground">
          {stock.industry || "—"} · {stock.exchange}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Card>
          <CardHeader>
            <CardTitle>TTM股息率</CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-semibold">{formatYield(stock.ttm_yield)}</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>静态股息率</CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-semibold">{formatYield(stock.static_yield)}</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>最新价</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-semibold">{formatPrice(stock.last_price)}</div>
            <div className="text-xs text-muted-foreground">更新于 {formatUpdatedAt(stock.price_updated_at)}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>总市值</CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-semibold">{formatMarketCap(stock.market_cap)}</CardContent>
        </Card>
      </div>

      {stock.ttm_yield_percentile !== null && (
        <p className="text-sm text-muted-foreground">
          当前TTM股息率高于近10年 {stock.ttm_yield_percentile}% 的历史时间
        </p>
      )}

      <div className="flex flex-wrap gap-4 text-sm text-muted-foreground">
        <span>PE(TTM): {stock.pe_ttm ?? "—"}</span>
        <span>PB: {stock.pb ?? "—"}</span>
        <span>连续分红年数: {stock.consecutive_dividend_years}</span>
      </div>

      <Card>
        <CardContent className="pt-4">
          <YieldChart code={code} />
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-4">
          <DividendTable dividends={dividends} />
        </CardContent>
      </Card>
    </div>
  );
}
