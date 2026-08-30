import { motion } from "framer-motion";

function Bone({ w = "100%", h = 14, style = {} }) {
  return <motion.div className="skeleton-bone" style={{ width: w, height: h, ...style }} />;
}

export function KpiSkeleton({ count = 8 }) {
  return (
    <div className="grid grid-4">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="card skeleton-card">
          <Bone h={28} w="45%" />
          <Bone h={10} w="60%" style={{ marginTop: 10 }} />
        </div>
      ))}
    </div>
  );
}

export function TableSkeleton({ rows = 6, cols = 4 }) {
  return (
    <div className="card skeleton-card">
      <Bone h={12} w="30%" style={{ marginBottom: 16 }} />
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="skeleton-row">
          {Array.from({ length: cols }).map((__, c) => (
            <Bone key={c} w={`${55 + (c % 3) * 15}%`} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function CardGridSkeleton({ count = 6 }) {
  return (
    <div className="vendor-grid">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="card skeleton-card" style={{ minHeight: 120 }}>
          <Bone h={16} w="70%" />
          <Bone h={10} w="50%" style={{ marginTop: 12 }} />
          <Bone h={4} w="100%" style={{ marginTop: 16 }} />
        </div>
      ))}
    </div>
  );
}

export function PageSkeleton({ variant = "table" }) {
  if (variant === "dashboard") {
    return (
      <>
        <Bone h={24} w={180} style={{ marginBottom: 8 }} />
        <KpiSkeleton />
        <div className="grid grid-2" style={{ marginTop: 16 }}>
          <div className="card skeleton-card" style={{ height: 380 }} />
          <div className="card skeleton-card" style={{ height: 280 }} />
        </div>
      </>
    );
  }
  if (variant === "grid") return <CardGridSkeleton />;
  return (
    <>
      <Bone h={24} w={160} style={{ marginBottom: 16 }} />
      <TableSkeleton />
    </>
  );
}
