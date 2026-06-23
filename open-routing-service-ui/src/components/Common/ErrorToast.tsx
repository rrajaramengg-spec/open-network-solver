import { useSearchStore } from '@/store';

/** Non-blocking error toast — does NOT clear previous results. */
export function ErrorToast(): JSX.Element | null {
  const lastError = useSearchStore((s) => s.lastError);
  const dismiss = useSearchStore((s) => s.dismissError);
  if (lastError === null) return null;
  return (
    <div
      role="alert"
      data-testid="error-toast"
      className="pointer-events-auto fixed bottom-4 left-1/2 z-50 -translate-x-1/2 rounded bg-rose-600 px-4 py-3 text-sm text-white shadow-lg"
    >
      <div className="flex items-center gap-3">
        <span className="font-medium">{lastError.message}</span>
        <button
          type="button"
          aria-label="Dismiss error"
          onClick={dismiss}
          className="rounded bg-rose-700 px-2 py-0.5 text-xs hover:bg-rose-800"
        >
          ×
        </button>
      </div>
    </div>
  );
}
