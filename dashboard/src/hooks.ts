import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

export type Route =
  | { page: "queue" }
  | { page: "claim"; claimId: string }
  | { page: "instructions" };

export function parseRoute(hash: string): Route {
  const [, page, id] = hash.replace(/^#/, "").split("/");
  if (page === "claims" && id) return { page: "claim", claimId: id };
  if (page === "instructions") return { page: "instructions" };
  return { page: "queue" };
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("hashchange", onChange);
  return () => {
    window.removeEventListener("hashchange", onChange);
  };
}

export function useRoute(): Route {
  return parseRoute(
    useSyncExternalStore(subscribe, () => window.location.hash),
  );
}

export interface Resource<T> {
  data: T | undefined;
  error: string | undefined;
  reload: () => void;
}

export function useResource<T>(
  path: string,
  load: (path: string) => Promise<T>,
  refreshMs: number | null,
): Resource<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string>();
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let active = true;
    const fetchNow = () => {
      load(path).then(
        (value) => {
          if (!active) return;
          setData(value);
          setError(undefined);
        },
        (reason: unknown) => {
          if (active)
            setError(reason instanceof Error ? reason.message : String(reason));
        },
      );
    };
    fetchNow();
    const timer =
      refreshMs === null ? undefined : setInterval(fetchNow, refreshMs);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [path, load, refreshMs, version]);

  const reload = useCallback(() => {
    setVersion((current) => current + 1);
  }, []);
  return { data, error, reload };
}
