import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  ComposedChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import type { DividendYieldPoint, YieldRange } from "@/types";

const RANGE_OPTIONS: { value: YieldRange; label: string }[] = [
  { value: "1y", label: "1年" },
  { value: "3y", label: "3年" },
  { value: "5y", label: "5年" },
  { value: "10y", label: "10年" },
  { value: "all", label: "全部" },
];

export function YieldChart({ code }: { code: string }) {
  const [range, setRange] = useState<YieldRange>("5y");
  const [data, setData] = useState<DividendYieldPoint[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api
      .get<{ data: DividendYieldPoint[] }>(`/stocks/${code}/yields?range=${range}`)
      .then((res) => setData(res.data))
      .finally(() => setLoading(false));
  }, [code, range]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-muted-foreground">历史股息率走势</h2>
        <div className="flex gap-1">
          {RANGE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              className={`rounded-md px-2 py-1 text-xs ${
                range === opt.value
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-muted"
              }`}
              onClick={() => setRange(opt.value)}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>
      {loading ? (
        <div className="flex h-72 items-center justify-center text-sm text-muted-foreground">加载中...</div>
      ) : data.length === 0 ? (
        <div className="flex h-72 items-center justify-center text-sm text-muted-foreground">暂无历史数据</div>
      ) : (
        <div className="h-72 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={data} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
              <XAxis
                dataKey="calc_date"
                tickFormatter={formatDate}
                tick={{ fontSize: 12 }}
              />
              <YAxis
                yAxisId="yield"
                tickFormatter={(v) => `${v}%`}
                tick={{ fontSize: 12 }}
                width={45}
              />
              <YAxis
                yAxisId="price"
                orientation="right"
                tick={{ fontSize: 12 }}
                width={45}
              />
              <Tooltip
                labelFormatter={formatDate}
                formatter={(value: number, name: string) =>
                  name === "price" ? [value, "股价"] : [`${value}%`, name === "ttm_yield" ? "TTM股息率" : "静态股息率"]
                }
              />
              <Legend
                formatter={(value) =>
                  value === "ttm_yield" ? "TTM股息率" : value === "static_yield" ? "静态股息率" : "股价"
                }
              />
              <Line
                yAxisId="yield"
                type="monotone"
                dataKey="ttm_yield"
                stroke="hsl(var(--primary))"
                strokeWidth={2}
                dot={false}
              />
              <Line
                yAxisId="yield"
                type="monotone"
                dataKey="static_yield"
                stroke="hsl(var(--success))"
                strokeWidth={2}
                dot={false}
              />
              <Line
                yAxisId="price"
                type="monotone"
                dataKey="price"
                stroke="hsl(var(--muted-foreground))"
                strokeWidth={1}
                strokeDasharray="4 2"
                dot={false}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
