import type { Metadata } from "next";

import { SettingsWorkbench } from "@/features/settings/settings-workbench";
import { getApplicationSettings, getProviderSettings, getProviderUsage, getScheduleConnection } from "@/lib/api";

export const metadata: Metadata = { title: "设置" };

export default async function SettingsPage() {
  const [settings, application, usage, connection] = await Promise.all([
    getProviderSettings(),
    getApplicationSettings(),
    getProviderUsage(),
    getScheduleConnection(),
  ]);
  return (
    <main className="page-stack">
      <SettingsWorkbench
        settings={settings.ok ? settings.data : null}
        application={application.ok ? application.data : null}
        usage={usage.ok ? usage.data : null}
        connection={connection.ok ? connection.data : null}
        backendError={!settings.ok ? settings.error : undefined}
      />
    </main>
  );
}
