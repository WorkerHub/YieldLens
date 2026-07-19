import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDate } from "@/lib/utils";
import type { Dividend } from "@/types";

function describeDividendPlan(d: Dividend): string {
  const parts: string[] = [];
  if (d.bonus_share_ratio) parts.push(`送${d.bonus_share_ratio}股`);
  if (d.transfer_ratio) parts.push(`转增${d.transfer_ratio}股`);
  parts.push(`派息${d.per_share_div.toFixed(4)}元`);
  return `10股${parts.join("")}(含税)`;
}

export function DividendTable({ dividends }: { dividends: Dividend[] }) {
  return (
    <div className="flex flex-col gap-3">
      <h2 className="text-sm font-medium text-muted-foreground">分红历史明细</h2>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>报告期</TableHead>
            <TableHead>状态</TableHead>
            <TableHead>分红方案</TableHead>
            <TableHead>股权登记日</TableHead>
            <TableHead>除权除息日</TableHead>
            <TableHead>派息日</TableHead>
            <TableHead>股息支付率</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {dividends.length === 0 && (
            <TableRow>
              <TableCell colSpan={7} className="py-8 text-center text-muted-foreground">
                暂无分红记录
              </TableCell>
            </TableRow>
          )}
          {dividends.map((d) => (
            <TableRow key={d.id}>
              <TableCell>{d.report_period.slice(0, 4)}年报</TableCell>
              <TableCell>
                <Badge variant={d.status === "implemented" ? "success" : "warning"}>
                  {d.status === "implemented" ? "实施" : "预案"}
                </Badge>
              </TableCell>
              <TableCell>{describeDividendPlan(d)}</TableCell>
              <TableCell>{formatDate(d.record_date)}</TableCell>
              <TableCell>{formatDate(d.ex_div_date)}</TableCell>
              <TableCell>{formatDate(d.pay_date)}</TableCell>
              <TableCell>{d.payout_ratio ? `${d.payout_ratio.toFixed(1)}%` : "—"}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
