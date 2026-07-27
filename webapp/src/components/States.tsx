interface ErrorStateProps {
  title?: string;
  message: string;
  onRetry?: () => void;
}

export function ErrorState({
  title = "Не удалось загрузить данные",
  message,
  onRetry,
}: ErrorStateProps): React.ReactElement {
  return (
    <div className="state-card state-card--error" role="alert">
      <span className="state-card__symbol">!</span>
      <div>
        <strong>{title}</strong>
        <p>{message}</p>
        {onRetry && (
          <button className="text-button" onClick={onRetry} type="button">
            Повторить
          </button>
        )}
      </div>
    </div>
  );
}

export function EmptyState({
  title,
  message,
}: {
  title: string;
  message: string;
}): React.ReactElement {
  return (
    <div className="empty-state">
      <span className="empty-state__mark" aria-hidden="true">
        ···
      </span>
      <strong>{title}</strong>
      <p>{message}</p>
    </div>
  );
}

export function SectionSkeleton({ rows = 3 }: { rows?: number }): React.ReactElement {
  return (
    <div aria-label="Загрузка" className="skeleton-list" role="status">
      {Array.from({ length: rows }, (_, index) => (
        <div className="skeleton" key={index}>
          <span />
          <span />
        </div>
      ))}
    </div>
  );
}
