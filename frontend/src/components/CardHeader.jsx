/** Card title row matching Flask template card-header pattern. */
export default function CardHeader({ title, badge, actions, className = "" }) {
  return (
    <div className={`card-header${className ? ` ${className}` : ""}`}>
      <span className="card-header-title">{title}</span>
      <span className="card-header-meta">
        {badge}
        {actions}
      </span>
    </div>
  );
}
