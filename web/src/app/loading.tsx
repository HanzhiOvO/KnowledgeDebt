export default function Loading() {
  return (
    <main className="page-stack wide-page loading-shell" aria-busy="true" aria-label="正在加载课程工作台">
      <span className="eyebrow">LOADING WORKBENCH</span>
      <div className="skeleton skeleton-title" />
      <div className="skeleton skeleton-line" />
      <div className="skeleton-grid">
        {Array.from({ length: 4 }, (_, index) => <div className="skeleton skeleton-card" key={index} />)}
      </div>
    </main>
  );
}
