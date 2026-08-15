import type { Metadata } from "next";

import { EmptyState } from "@/components/empty-state";
import { HomeDashboard } from "@/features/home/home-dashboard";
import { getHome } from "@/lib/api";

export const metadata: Metadata = { title: "今天" };

export default async function Home() {
  const result = await getHome();

  return (
    <main className="page-stack">
      {result.ok ? (
        <HomeDashboard home={result.data} />
      ) : (
        <EmptyState
          eyebrow="BACKEND OFFLINE"
          title="连接后端后开始偿还知识债务"
          detail={result.error}
          actionHref="/courses"
          actionLabel="先查看课程"
        />
      )}
    </main>
  );
}
