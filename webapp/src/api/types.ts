export type UserRole = "admin" | "viewer";

export interface WebAppUser {
  id: number;
  first_name?: string;
  last_name?: string;
  username?: string;
  display_name?: string;
  role: UserRole;
  is_admin?: boolean;
}

export interface TabConfig {
  id: string;
  kind?: string;
  title?: string;
  enabled?: boolean;
  order?: number;
  required_role?: UserRole;
}

export type CameraHealth = "online" | "offline" | "degraded" | "unknown";

export interface CameraHealthDetails {
  state: CameraHealth | string;
  available?: boolean;
  reason?: string | null;
  last_segment_at?: string | null;
  last_segment_age_sec?: number | null;
}

export interface Camera {
  id: string;
  title: string;
  health?: CameraHealth | CameraHealthDetails;
  online?: boolean;
  status?: string;
  last_seen_at?: string | null;
  live_available?: boolean;
}

export interface ClimateRoomDefinition {
  id: string;
  title: string;
}

export interface BootstrapResponse {
  user: WebAppUser;
  tabs?: Array<TabConfig | string>;
  cameras: Camera[];
  climate_rooms?: ClimateRoomDefinition[];
}

export interface SessionResponse {
  access_token: string;
  token_type: "bearer";
  user: WebAppUser;
  expires_at: string;
}

export interface CamerasResponse {
  items?: Camera[];
  cameras?: Camera[];
}

export interface VideoRecording {
  id: string | number;
  camera_id: string;
  camera_title?: string;
  created_at: string;
  duration_sec?: number | null;
  size_bytes?: number | null;
  filename?: string;
  playable?: boolean;
  content_url?: string;
  download_url?: string;
}

export interface VideosResponse {
  items: VideoRecording[];
  next_cursor?: string | null;
  has_more?: boolean;
}

export type RecordingStatus = "queued" | "running";
export type RecordingPhase = "queued" | "recording" | "finalizing" | "stale";

export interface RecordingActivity {
  job_id: string;
  camera_id: string;
  status: RecordingStatus;
  phase: RecordingPhase;
  duration_sec: number;
  created_at: string;
  started_at?: string | null;
  expected_finish_at?: string | null;
}

export interface RecordingsResponse {
  items: RecordingActivity[];
  recent_results: RecordingResult[];
  generated_at: string;
}

export interface RecordingResult {
  job_id: string;
  camera_id: string;
  status: "done" | "failed";
  finished_at: string;
  video_id?: number | null;
}

export interface StartRecordingResponse {
  job_id: string;
  camera_id: string;
  duration_sec: number;
  status: "queued";
  phase: "queued";
  created_at: string;
}

export interface DownloadTicket {
  url?: string;
  download_url?: string;
  content_url?: string;
  filename?: string;
  expires_at?: string;
}

export interface StreamTicket {
  ws_url?: string;
  url?: string;
  hls_url?: string;
  player_script_url?: string;
  modes?: string[];
  media?: string;
  expires_at?: string;
}

export interface ClimateReading {
  id?: string;
  room_id: string;
  room_title?: string;
  title?: string;
  temperature_c?: number | null;
  humidity_percent?: number | null;
  updated_at?: string | null;
  stale?: boolean;
  temperature?: ClimateMetricReading | null;
  humidity?: ClimateMetricReading | null;
}

export interface ClimateMetricReading {
  value: number;
  unit: string;
  updated_at?: string | null;
  stale?: boolean;
}

export interface ClimateCurrentResponse {
  rooms?: ClimateReading[];
  items?: ClimateReading[];
  generated_at?: string;
}

export interface ClimateHistoryPoint {
  timestamp: string;
  temperature_c?: number | null;
  humidity_percent?: number | null;
}

export interface ClimateHistoryResponse {
  room_id: string;
  from?: string;
  to?: string;
  bucket_sec?: number;
  points?: ClimateHistoryPoint[];
  series?: ClimateHistorySeries[];
}

export interface ClimateHistorySeries {
  metric: "temperature" | "humidity" | string;
  unit: string;
  points: Array<{
    timestamp: string;
    value: number;
    sample_count?: number;
  }>;
}

export type CollectionResponse<T> = T[] | { items?: T[] };
