import { useCallback, useEffect, useState } from "react";

export function useAsync(loader, deps = []) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    loader()
      .then((result) => {
        setData(result);
        setLoading(false);
      })
      .catch((e) => {
        setError(e.message || "Request failed");
        setLoading(false);
      });
  }, deps);

  useEffect(() => {
    reload();
  }, [reload]);

  return { data, loading, error, reload, setData };
}
