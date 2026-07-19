import { Navigate, Route, Routes } from "react-router";
import { AppLayout } from "@/components/layout/AppLayout";
import { RankingPage } from "@/pages/ranking/RankingPage";
import { StockDetailPage } from "@/pages/stock/StockDetailPage";

export function App() {
  return (
    <AppLayout>
      <Routes>
        <Route path="/" element={<RankingPage />} />
        <Route path="/stocks/:code" element={<StockDetailPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppLayout>
  );
}
