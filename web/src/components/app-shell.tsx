"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const navigation = [
  { href: "/", label: "总览", glyph: "⌂" },
  { href: "/schedule", label: "课表", glyph: "▦" },
  { href: "/courses", label: "课程", glyph: "◫" },
  { href: "/review", label: "待审核", glyph: "◎" },
  { href: "/settings", label: "设置", glyph: "⚙" },
];

function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link className="brand" href="/" aria-label="KnowledgeDebt 首页">
          <span className="brand-mark" aria-hidden>K</span>
          <span>
            <strong>KnowledgeDebt</strong>
            <small>自动化课程工作台 · v0.2</small>
          </span>
        </Link>
        <nav className="nav-list" aria-label="主导航">
          {navigation.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={isActive(pathname, item.href) ? "nav-item active" : "nav-item"}
            >
              <span aria-hidden>{item.glyph}</span>
              {item.label}
            </Link>
          ))}
        </nav>
        <Link className="quick-capture" href="/review#inbox">
          <span aria-hidden>＋</span>
          <span><strong>快速收件</strong><small>无需先选课程</small></span>
        </Link>
        <div className="sidebar-note">
          <span className="status-dot" />
          <div>
            <strong>Local-first</strong>
            <small>外发前逐次确认</small>
          </div>
        </div>
      </aside>
      <div className="app-content">{children}</div>
      <nav className="mobile-nav" aria-label="移动端导航">
        {navigation.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={isActive(pathname, item.href) ? "active" : ""}
          >
            <span aria-hidden>{item.glyph}</span>
            <small>{item.label}</small>
          </Link>
        ))}
      </nav>
    </div>
  );
}
