import PageTransition from "./PageTransition";
import Loader from "./Loader";
import { PageSkeleton } from "./Skeleton";

export default function PageShell({
  title,
  titleIcon,
  subtitle,
  actions,
  loading,
  error,
  skeleton = "table",
  children,
}) {
  if (loading) {
    return (
      <PageTransition>
        <div className="page-loading-overlay">
          <PageSkeleton variant={skeleton} />
          <Loader label="Loading — waiting for data" />
        </div>
      </PageTransition>
    );
  }

  if (error) {
    return (
      <PageTransition>
        <div className="alert alert-error">{error}</div>
      </PageTransition>
    );
  }

  return (
    <PageTransition>
      {(title || actions) && (
        <div className="page-header">
          <div>
            {title && (
              <div className="page-title-row">
                {titleIcon}
                <h1 className="page-title">{title}</h1>
              </div>
            )}
            {subtitle && <p className="page-sub">{subtitle}</p>}
          </div>
          {actions}
        </div>
      )}
      <div className="page-stack">{children}</div>
    </PageTransition>
  );
}
