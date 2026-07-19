import { useNavigate } from "react-router";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDate, formatMarketCap, formatPrice, formatYield } from "@/lib/utils";
import type { Stock, StockListParams } from "@/types";

interface StockTableProps {
  stocks: Stock[];
  sort: StockListParams["sort"];
  order: StockListParams["order"];
  onSortChange: (sort: StockListParams["sort"]) => void;
}

const SORTABLE_COLUMNS: { key: StockListParams["sort"]; label: string }[] = [
  { key: "ttm_yield", label: "TTM股息率" },
  { key: "static_yield", label: "静态股息率" },
  { key: "market_cap", label: "总市值(亿)" },
];

export function StockTable({ stocks, sort, order, onSortChange }: StockTableProps) {
  const navigate = useNavigate();

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>代码/名称</TableHead>
          <TableHead>最新价</TableHead>
          {SORTABLE_COLUMNS.map((col) => (
            <TableHead key={col.key}>
              <button
                type="button"
                className="flex items-center gap-1 hover:text-foreground"
                onClick={() => onSortChange(col.key)}
              >
                {col.label}
                {sort === col.key && <span>{order === "desc" ? "↓" : "↑"}</span>}
              </button>
            </TableHead>
          ))}
          <TableHead>行业</TableHead>
          <TableHead>最近分红日</TableHead>
          <TableHead>连续分红年</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {stocks.length === 0 && (
          <TableRow>
            <TableCell colSpan={8} className="py-8 text-center text-muted-foreground">
              没有匹配的股票
            </TableCell>
          </TableRow>
        )}
        {stocks.map((s) => (
          <TableRow
            key={s.code}
            className="cursor-pointer"
            onClick={() => navigate(`/stocks/${s.code}`)}
          >
            <TableCell>
              <div className="font-medium">{s.name}</div>
              <div className="text-xs text-muted-foreground">
                {s.code}
                {s.is_st === 1 && (
                  <Badge variant="warning" className="ml-1">
                    ST
                  </Badge>
                )}
              </div>
            </TableCell>
            <TableCell>{formatPrice(s.last_price)}</TableCell>
            <TableCell className={sort === "ttm_yield" ? "font-medium" : undefined}>
              {formatYield(s.ttm_yield)}
            </TableCell>
            <TableCell className={sort === "static_yield" ? "font-medium" : undefined}>
              {formatYield(s.static_yield)}
            </TableCell>
            <TableCell className={sort === "market_cap" ? "font-medium" : undefined}>
              {formatMarketCap(s.market_cap)}
            </TableCell>
            <TableCell>{s.industry || "—"}</TableCell>
            <TableCell>{formatDate(s.last_dividend_date)}</TableCell>
            <TableCell>{s.consecutive_dividend_years}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
