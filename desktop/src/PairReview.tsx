import { useEffect, useMemo, useState } from "react";

const ENGINE_URL = "http://127.0.0.1:8765";
const PAGE_SIZE = 12;

export type PairedArtwork = {
  pair_key: string;
  source_paths: string[];
  target_path: string;
  pairing_method: string;
  target_duplicate_paths: string[];
};

function filename(value: string) {
  return value.split(/[\\/]/).pop() ?? value;
}

export default function PairReview({
  token,
  pairs,
  onReviewProgress
}: {
  token: string;
  pairs: PairedArtwork[];
  onReviewProgress?: (checked: number) => void;
}) {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<"all" | "visual" | "unreviewed">("all");
  const [page, setPage] = useState(0);
  const [reviewed, setReviewed] = useState<Set<number>>(new Set());
  const [expanded, setExpanded] = useState(true);
  const [zoom, setZoom] = useState<{ pairIndex: number; sourceIndex: number } | null>(null);

  useEffect(() => {
    setSearch("");
    setFilter("all");
    setPage(0);
    setReviewed(new Set());
    setZoom(null);
    onReviewProgress?.(0);
  }, [token]);

  useEffect(() => {
    if (!zoom) return;
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setZoom(null);
    };
    window.addEventListener("keydown", onEscape);
    return () => window.removeEventListener("keydown", onEscape);
  }, [zoom]);

  const visible = useMemo(
    () => pairs.map((pair, index) => ({ pair, index })).filter(({ pair, index }) => {
      if (filter === "visual" && pair.pairing_method !== "visual_fallback") return false;
      if (filter === "unreviewed" && reviewed.has(index)) return false;
      if (!search.trim()) return true;
      const query = search.trim().toLowerCase();
      return [pair.pair_key, pair.target_path, ...pair.source_paths].some((value) =>
        value.toLowerCase().includes(query)
      );
    }),
    [pairs, filter, search, reviewed]
  );
  const lastPage = Math.max(0, Math.ceil(visible.length / PAGE_SIZE) - 1);
  const currentPage = Math.min(page, lastPage);
  const pagePairs = visible.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);

  const markReviewed = (index: number, checked: boolean) => {
    setReviewed((current) => {
      const updated = new Set(current);
      if (checked) updated.add(index);
      else updated.delete(index);
      onReviewProgress?.(updated.size);
      return updated;
    });
  };

  function thumbnail(index: number, role: "source" | "target", sourceIndex = 0, size = "thumb") {
    return `${ENGINE_URL}/historical/onboarding/previews/${encodeURIComponent(token)}/${index}/${role}?source_index=${sourceIndex}&size=${size}`;
  }

  return (
    <section className="pair-review">
      <div className="pair-review-header">
        <div>
          <strong>Source → Final visual review</strong>
          <p>{reviewed.size} of {pairs.length} pairs marked reviewed · Thumbnails are read-only</p>
        </div>
        <button className="dataset-details-toggle" onClick={() => setExpanded((value) => !value)}>
          {expanded ? "Hide pairs" : `Review pairs (${pairs.length})`}
        </button>
      </div>
      {expanded && (
        <>
          <div className="pair-review-toolbar">
            <input
              aria-label="Search pair key or filename"
              value={search}
              onChange={(event) => { setSearch(event.target.value); setPage(0); }}
              placeholder="Search design ID or filename"
            />
            <select
              aria-label="Pair review filter"
              value={filter}
              onChange={(event) => { setFilter(event.target.value as typeof filter); setPage(0); }}
            >
              <option value="all">All pairs</option>
              <option value="visual">Visual fallback</option>
              <option value="unreviewed">Not yet reviewed</option>
            </select>
          </div>
          {pagePairs.length === 0 ? (
            <p className="dataset-muted">No matching pairs for this filter.</p>
          ) : (
            <div className="pair-review-grid">
              {pagePairs.map(({ pair, index }) => (
                <article className="pair-review-card" key={index}>
                  <div className="pair-review-title">
                    <strong title={pair.pair_key}>{pair.pair_key}</strong>
                    {pair.pairing_method === "visual_fallback" && (
                      <span className="pair-method">Visual match</span>
                    )}
                  </div>
                  <div className="pair-review-images">
                    <div className="pair-image-column">
                      <div className="pair-image-label">SOURCE</div>
                      <div className="pair-source-image-grid">
                        {pair.source_paths.slice(0, 3).map((path, sourceIndex) => (
                          <button
                            className="pair-image-button"
                            key={sourceIndex}
                            title={path}
                            aria-label={`Enlarge source ${sourceIndex + 1} for ${pair.pair_key}`}
                            onClick={() => setZoom({ pairIndex: index, sourceIndex })}
                          >
                            <img
                              loading="lazy"
                              src={thumbnail(index, "source", sourceIndex)}
                              alt={`Source ${sourceIndex + 1}: ${filename(path)}`}
                            />
                          </button>
                        ))}
                      </div>
                      <small title={pair.source_paths.join("\n")}>
                        {pair.source_paths.length} view{pair.source_paths.length !== 1 ? "s" : ""}
                      </small>
                    </div>
                    <span className="pair-arrow" aria-hidden="true">→</span>
                    <div className="pair-image-column">
                      <div className="pair-image-label">FINAL</div>
                      <button
                        className="pair-image-button"
                        title={pair.target_path}
                        aria-label={`Enlarge final artwork for ${pair.pair_key}`}
                        onClick={() => setZoom({ pairIndex: index, sourceIndex: 0 })}
                      >
                        <img
                          className="pair-final-image"
                          loading="lazy"
                          src={thumbnail(index, "target")}
                          alt={`Final artwork: ${filename(pair.target_path)}`}
                        />
                      </button>
                      <small title={pair.target_path}>{filename(pair.target_path)}</small>
                    </div>
                  </div>
                  <div className="pair-review-footer">
                    <label>
                      <input
                        type="checkbox"
                        checked={reviewed.has(index)}
                        onChange={(event) => markReviewed(index, event.target.checked)}
                      />
                      Reviewed
                    </label>
                    {pair.target_duplicate_paths.length > 0 && (
                      <span title={pair.target_duplicate_paths.join("\n")}>
                        +{pair.target_duplicate_paths.length} identical finals
                      </span>
                    )}
                  </div>
                </article>
              ))}
            </div>
          )}
          {visible.length > PAGE_SIZE && (
            <div className="pair-review-pagination">
              <span>{currentPage * PAGE_SIZE + 1}–{Math.min((currentPage + 1) * PAGE_SIZE, visible.length)} of {visible.length}</span>
              <button disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous</button>
              <button disabled={currentPage === lastPage} onClick={() => setPage(currentPage + 1)}>Next</button>
            </div>
          )}
        </>
      )}
      {zoom && pairs[zoom.pairIndex] && (
        <div className="pair-lightbox" role="presentation" onClick={() => setZoom(null)}>
          <div
            className="pair-lightbox-panel"
            role="dialog"
            aria-modal="true"
            aria-label={`Review ${pairs[zoom.pairIndex].pair_key}`}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="pair-lightbox-head">
              <strong>{pairs[zoom.pairIndex].pair_key}</strong>
              <button onClick={() => setZoom(null)} aria-label="Close image comparison">×</button>
            </div>
            <div className="pair-lightbox-grid">
              <div>
                <div className="pair-image-label">SOURCE</div>
                <img
                  src={thumbnail(zoom.pairIndex, "source", zoom.sourceIndex, "large")}
                  alt="Selected source image"
                />
                <small>{filename(pairs[zoom.pairIndex].source_paths[zoom.sourceIndex])}</small>
                {pairs[zoom.pairIndex].source_paths.length > 1 && (
                  <div className="pair-lightbox-views">
                    {pairs[zoom.pairIndex].source_paths.map((_, sourceIndex) => (
                      <button
                        key={sourceIndex}
                        className={zoom.sourceIndex === sourceIndex ? "selected" : ""}
                        onClick={() => setZoom({ ...zoom, sourceIndex })}
                      >
                        View {sourceIndex + 1}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <div>
                <div className="pair-image-label">APPROVED FINAL</div>
                <img
                  src={thumbnail(zoom.pairIndex, "target", 0, "large")}
                  alt="Approved final artwork"
                />
                <small>{filename(pairs[zoom.pairIndex].target_path)}</small>
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
