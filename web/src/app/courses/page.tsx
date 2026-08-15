import type { Metadata } from "next";

import { EmptyState } from "@/components/empty-state";
import { CourseForm } from "@/features/courses/course-form";
import { CourseList } from "@/features/courses/course-list";
import { getCourses } from "@/lib/api";

export const metadata: Metadata = { title: "课程" };

export default async function CoursesPage() {
  const result = await getCourses();
  return (
    <main className="page-stack">
      <header className="page-header">
        <div><span className="eyebrow">COURSES</span><h1>课程与真实课堂</h1><p>Session 是容器，录音只是一种可选证据。</p></div>
        <CourseForm />
      </header>
      {result.ok ? <CourseList courses={result.data} /> : (
        <EmptyState eyebrow="BACKEND OFFLINE" title="暂时无法读取课程" detail={result.error} />
      )}
    </main>
  );
}
