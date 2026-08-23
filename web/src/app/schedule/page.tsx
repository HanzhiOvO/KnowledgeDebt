import type { Metadata } from "next";

import { EmptyState } from "@/components/empty-state";
import { ScheduleWorkbench } from "@/features/schedule/schedule-workbench";
import { getApplicationSettings, getSchedule, getScheduleConnection } from "@/lib/api";

export const metadata: Metadata = { title: "课表" };

export default async function SchedulePage() {
  const [schedule, connection, application] = await Promise.all([
    getSchedule(),
    getScheduleConnection(),
    getApplicationSettings(),
  ]);
  if (!schedule.ok && !connection.ok) {
    return <main className="page-stack"><EmptyState eyebrow="SCHEDULE OFFLINE" title="暂时无法读取课表" detail={schedule.error} /></main>;
  }
  return (
    <main className="page-stack wide-page">
      <ScheduleWorkbench
        initialOccurrences={schedule.ok ? schedule.data : []}
        connection={connection.ok ? connection.data : null}
        timezone={application.ok ? application.data.timezone : "Asia/Shanghai"}
      />
    </main>
  );
}
