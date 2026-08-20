import type { Metadata } from "next";

import { ReviewWorkbench } from "@/features/review/review-workbench";
import { getHome, getInbox, getReviews } from "@/lib/api";

export const metadata: Metadata = { title: "待审核" };

export default async function ReviewPage() {
  const [reviews, inbox, home] = await Promise.all([getReviews(), getInbox(), getHome()]);
  return (
    <main className="page-stack">
      <ReviewWorkbench
        reviews={reviews.ok ? reviews.data : []}
        inbox={inbox.ok ? inbox.data : []}
        sessions={home.ok ? home.data.sessions : []}
        backendError={!reviews.ok ? reviews.error : !inbox.ok ? inbox.error : undefined}
      />
    </main>
  );
}

