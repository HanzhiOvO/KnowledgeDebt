"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const navigation = [
  { href: "/", label: "今天", glyph: "⌂" },
  { href: "/courses", label: "课程", glyph: "◇" },
  { href: "/debts", label: "债务", glyph: "↗" },
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
        <Link className="brand" href="/" aria-label="知债首页">
          <span className="brand-mark">知</span>
          <span>
            <strong>知债</strong>
            <small>KnowledgeDebt · 知识债务</small>
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
        <div className="sidebar-note">
          <span className="status-dot" />
          <div>
            <strong>本地优先</strong>
            <small>资料默认留在你的服务器</small>
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
