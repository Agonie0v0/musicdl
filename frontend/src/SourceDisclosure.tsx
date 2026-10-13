import { useRef, type ReactNode } from "react";

export function SourceDisclosure({
  summary,
  children,
}: {
  summary: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDetailsElement>(null);
  return (
    <details className="source-disclosure" ref={ref}>
      <summary>
        {summary}
        <span>展开 / 收起</span>
      </summary>
      <div className="source-disclosure-content">
        {children}
        <button
          type="button"
          className="secondary"
          onClick={() => {
            if (ref.current) {
              ref.current.open = false;
              ref.current.querySelector("summary")?.focus();
            }
          }}
        >
          完成选择，收起
        </button>
      </div>
    </details>
  );
}
