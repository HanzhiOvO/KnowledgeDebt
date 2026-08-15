import Link from "next/link";

import type { Course } from "@/types/domain";

export function CourseList({ courses }: { courses: Course[] }) {
  if (!courses.length) {
    return <div className="panel empty-list"><strong>还没有课程</strong><p>创建课程后，即使没有任何录音或资料，也可以先建立 Session。</p></div>;
  }

  return (
    <div className="course-grid">
      {courses.map((course, index) => (
        <Link className="course-card panel" href={`/courses/${course.id}`} key={course.id}>
          <span className={`course-index tone-${index % 4}`}>{String(index + 1).padStart(2, "0")}</span>
          <div>
            <span className="eyebrow">{course.semester || "CURRENT COURSE"}</span>
            <h2>{course.name}</h2>
            <p>{course.description || "尚未添加课程说明"}</p>
          </div>
          <span className="arrow">→</span>
        </Link>
      ))}
    </div>
  );
}
