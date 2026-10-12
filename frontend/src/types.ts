export interface Quality {
  id: string;
  label: string;
  codec: string;
  bitrate: number | null;
  lossless: boolean;
  availability: string;
  evidence: string;
}
export interface Track {
  id: string;
  source: string;
  source_id: string;
  title: string;
  artist: string;
  album: string;
  duration: number;
  cover: string | null;
  size_bytes?: number | null;
  size_label?: string | null;
  episodes?: number;
  root_source?: string;
  qualities: Quality[];
  playback: {
    state: string;
    url: string | null;
    codec: string | null;
    bitrate: number | null;
    detail: string | null;
    preview_only: boolean;
  };
}
export interface Config {
  available_sources?: {
    id: string;
    label: string;
    category?: string;
    engine_name?: string;
    proxy_recommended?: boolean;
    init_error?: string | null;
  }[];
  engine?: string;
  sources: string[];
  results_per_source: number;
  search_timeout_seconds: number;
  playback_timeout_seconds: number;
  download_concurrency: number;
  download_layout: string;
  lyrics_mode: string;
  embed_metadata: boolean;
  source_settings: Record<string, { cookie: string; proxy: string }>;
}
export interface DownloadJob {
  id: string;
  track: Track;
  quality_id: string;
  quality_label: string;
  status: string;
  bytes: number;
  total_bytes: number | null;
  detail: string;
  file: string | null;
}
export interface LibraryFile {
  path: string;
  name: string;
  size: number;
  root?: string;
  folder?: string;
  track?: Track;
  editable?: boolean;
}
export interface SourceStatus {
  state: string;
  count?: number;
  detail?: string;
}
export interface PlaylistIssue {
  source_id: string;
  title: string;
  artist: string;
  reason: string;
}
export interface SavedPlaylist {
  id: string;
  name: string;
  source: string;
  source_url: string;
  created_at: number;
  tracks: Track[];
  issues: PlaylistIssue[];
  detail: string;
  task_id: string;
}
export interface PlaylistSummary extends Omit<
  SavedPlaylist,
  "tracks" | "issues"
> {
  track_count: number;
  issue_count: number;
  cover: string | null;
}
